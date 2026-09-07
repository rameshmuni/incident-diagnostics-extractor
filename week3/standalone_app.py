"""
Optional, laptop-only convenience: run just Week 3's demo app (login + upload pages, plus
its break/reset controls) on its own local Flask server - none of Week 2's dependencies
(no BigQuery, no Gemini, no ServiceNow credentials) are needed to use it. Handy for quickly
poking at the broken app and watching a toggle or a real chmod fault take effect, without
spinning up the full assistant.

This is NOT part of the deployed Cloud Run image and changes nothing about the real
deployment - the actual Week 3 demo, wired into auto-remediation and ServiceNow, only runs
through the top-level app.py / Cloud Run service. This script exists purely so you can click
around locally without that whole stack running.

There's no "fix" here, on purpose, matching the real app.py: fixing a scenario back to
healthy only ever happens by reporting the symptom through the chat with human-in-the-loop
off (see attempt_auto_remediation() in auto_remediation.py), which needs real Gemini/
ServiceNow credentials this standalone runner deliberately doesn't have.

Usage - either of these works:
    cd scripts/  &&  python3 -m week3.standalone_app
    cd scripts/week3/  &&  python3 standalone_app.py
Then open http://localhost:5002/demo/login
"""

from flask import Flask, jsonify, redirect, request

try:
    # normal case: run as part of the week3 package (python3 -m week3.standalone_app)
    from . import mock_systems as ms
    from .demo_app import demo_bp
except ImportError:
    # also allow running this file directly (cd week3/ && python3 standalone_app.py) -
    # mock_systems.py and demo_app.py are right next to this file either way
    import mock_systems as ms
    from demo_app import demo_bp

app = Flask(__name__)
app.register_blueprint(demo_bp)
ms.ensure_upload_dir()


@app.route("/")
def home():
    return redirect("/demo/login")


# the same tiny control routes the real app.py exposes, so this standalone runner can
# demo the tray's break/reset buttons too without needing the rest of the assistant running
@app.route("/api/demo/state")
def demo_state():
    return jsonify(ms.get_state())


@app.route("/api/demo/break", methods=["POST"])
def demo_break():
    # no ServiceNow here, ever - this file's whole point is a zero-credentials local runner,
    # and mirrors app.py's own /api/demo/break: state-only, nothing else. There's no
    # /api/demo/fix here (or in app.py) - fixing a scenario back to healthy is a human's job,
    # done by reporting the symptom through the chat with human-in-the-loop off (see
    # attempt_auto_remediation() in auto_remediation.py), which this standalone runner can't
    # do anyway since it has no Gemini/ServiceNow credentials.
    try:
        data = request.get_json(force=True)
        state = ms.break_issue(data.get("issue_id"))
        return jsonify({"ok": True, "state": state})
    except Exception as exc:  # noqa: BLE001 - surfacing the real error is the point
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/demo/reset", methods=["POST"])
def demo_reset():
    return jsonify({"ok": True, "state": ms.reset_all()})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002, debug=True)
