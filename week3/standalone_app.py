"""
Optional, laptop-only convenience: run just Week 3's demo app (login + upload pages, plus
its break/fix/reset controls) on its own local Flask server - none of Week 2's dependencies
(no BigQuery, no Gemini, no ServiceNow credentials) are needed to use it. Handy for quickly
poking at the broken app and watching a toggle or a real chmod fault take effect, without
spinning up the full assistant.

This is NOT part of the deployed Cloud Run image and changes nothing about the real
deployment - the actual Week 3 demo, wired into auto-remediation and ServiceNow, only runs
through the top-level app.py / Cloud Run service. This script exists purely so you can click
around locally without that whole stack running.

Usage - either of these works:
    cd scripts/  &&  python3 -m week3.standalone_app
    cd scripts/week3/  &&  python3 standalone_app.py
Then open http://localhost:5002/demo/login
"""

from flask import Flask, jsonify, redirect, request

try:
    # normal case: run as part of the week3 package (python3 -m week3.standalone_app)
    from . import mock_systems as ms
    from . import remedies
    from .demo_app import demo_bp
except ImportError:
    # also allow running this file directly (cd week3/ && python3 standalone_app.py) -
    # mock_systems.py, remedies.py and demo_app.py are right next to this file either way
    import mock_systems as ms
    import remedies
    from demo_app import demo_bp

app = Flask(__name__)
app.register_blueprint(demo_bp)
ms.ensure_upload_dir()

# local-only version of auto_remediation.py's CATALOG->remedy lookup, deliberately NOT
# imported from auto_remediation.py itself - that module imports query_incident.py and
# seed_resolved_incidents.py, which need real Gemini/ServiceNow credentials just to import
# (seed_resolved_incidents.py reads required env vars at module load time). Pulling any of
# that in here would break this file's whole point: works with zero GCP/ServiceNow setup.
_REMEDY_BY_ISSUE = {
    "app_down": remedies.fix_app_down,
    "login_ui_disabled": remedies.fix_login_ui_disabled,
    "login_cred_backend": remedies.fix_login_cred_backend,
    "upload_permission": remedies.fix_upload_permission,
}


@app.route("/")
def home():
    return redirect("/demo/login")


# the same 3 tiny control routes the real app.py exposes, so this standalone runner can
# demo the break/fix/reset buttons too without needing the rest of the assistant running
@app.route("/api/demo/state")
def demo_state():
    return jsonify(ms.get_state())


@app.route("/api/demo/break", methods=["POST"])
def demo_break():
    # no ServiceNow here on purpose (see this file's docstring) - state-only break, same as
    # before. The real event-raising step only exists in the deployed app.py.
    try:
        data = request.get_json(force=True)
        state = ms.break_issue(data.get("issue_id"))
        return jsonify({"ok": True, "state": state, "event": None, "event_error": None})
    except Exception as exc:  # noqa: BLE001 - surfacing the real error is the point
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/demo/fix", methods=["POST"])
def demo_fix():
    # mirrors app.py's /api/demo/fix, minus the ServiceNow resolve step - just runs the real
    # remedy function so the tray's "Fix" buttons work locally too
    try:
        data = request.get_json(force=True)
        issue_id = data.get("issue_id")
        remedy_fn = _REMEDY_BY_ISSUE.get(issue_id)
        if remedy_fn is None:
            raise ValueError(f"unknown issue_id {issue_id!r}")
        resolution_text = remedy_fn(issue_id)
        return jsonify({
            "ok": True,
            "state": ms.get_state(),
            "event": {"resolution_text": resolution_text, "incident_number": None},
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/demo/reset", methods=["POST"])
def demo_reset():
    return jsonify({"ok": True, "state": ms.reset_all()})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002, debug=True)
