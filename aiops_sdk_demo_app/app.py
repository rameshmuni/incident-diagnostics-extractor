"""
A second, complete, running copy of the AIOps Virtual Agent - functionally
identical to the original app.py, but every place the original imports
query_incident / query_incident_gemini / week3.auto_remediation functions
directly, this one goes through a single `AiopsSDK` object instead.

Why this file exists: aiops_sdk/examples/verify_sdk.py already proves the
SDK's wiring is correct with mocked calls. This proves the same thing with a
real, running app - point a browser at it and it behaves exactly like the
original, powered by aiops_sdk underneath instead of six separate imports.

Nothing about the original project changed to build this. This is a new
file, in a new folder, that imports the existing project's own code (via
aiops_sdk, and via week3.demo_app/week3.mock_systems directly for the demo
pages - see below) - it does not copy or reimplement any of it. app.py,
week3/*.py, query_incident*.py and seed_resolved_incidents.py are all
untouched; run `git status` and only aiops_sdk/ and aiops_sdk_demo_app/ show
as new.

Why /demo (login + upload) is imported, not duplicated: the SDK wraps the
RAG / remediation / ServiceNow logic - the parts that were genuinely spread
across several scripts. The /demo pages are one self-contained blueprint
that was never spread across scripts in the first place, so there's nothing
for an SDK to consolidate there. Reusing week3.demo_app.demo_bp as-is (an
import, not a copy) also means "break app_down" from either app's demo tray
is visible in both - they're two front doors to the same underlying system,
which is the whole point of a duplicate that's meant to be compared side by
side with the original, not a fork of it.

Run it (from the repo root, or from anywhere - see the sys.path line below):
    python3 aiops_sdk_demo_app/app.py
Defaults to port 5002 (the original defaults to 5001) so both can run at the
same time locally if you want to demo them side by side.

One deliberate behavior difference from the original: this app has NO CODE
PATH that can run a remedy script, period. The original runs a matched
remedy immediately, with no one confirming it first. This app's "Check Known
Fixes" mode matches against the exact same catalog, but the only thing it
ever does with a confident match is call sdk.raise_remedy_candidate() -
which opens a real, unresolved ServiceNow incident and returns. Nothing
downstream of that call in this file ever executes a fix. The other half of
that story - reviewing what's waiting and approving it - lives in a
separate app, aiops_console_app/, which is the only place
sdk.run_confirmed_remedy_for_candidate() is ever called. That split is
deliberate: the person who merely reports a symptom and the person
authorized to run a script against production infrastructure are not
assumed to be the same person.
"""

import os
import sys
from pathlib import Path

# Put the repo root on sys.path so `import aiops_sdk` and `import week3` work
# no matter what directory this is launched from - the same trick
# aiops_sdk/examples/verify_sdk.py already uses, for the same reason.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, request, send_from_directory

from aiops_sdk import AiopsSDK
from week3.demo_app import demo_bp
from week3 import mock_systems

app = Flask(__name__, static_folder="static", static_url_path="")

# Same blueprint the original app registers - see the module docstring above
# for why this one piece is reused rather than re-wrapped.
app.register_blueprint(demo_bp)

# Same idempotent guard the original app.py runs at startup.
mock_systems.ensure_upload_dir()

# One AiopsSDK instance, built once at import time, used by every route below.
# This single object is standing in for the 6 separate imports
# (build_prompt, corpus_size, embed_query, escalate_to_developer,
# log_feedback, parse_slm_pick, call_gemini, attempt_auto_remediation) the
# original app.py has at the top of its file - that collapse from "six named
# functions from four files" to "one object" is the concrete thing an SDK
# buys you, and this app is the proof it still behaves the same afterward.
sdk = AiopsSDK()


@app.route("/")
def home():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/status")
def status():
    # Same live BigQuery connectivity check as the original, via
    # sdk.search.corpus_size() instead of a bare corpus_size() import; model
    # name comes from the provider object instead of a separate GEMINI_MODEL
    # import - swap sdk = AiopsSDK(llm_provider=SomeOtherProvider()) above
    # and this line keeps working with no other change.
    try:
        return jsonify({"ok": True, "corpus_size": sdk.search.corpus_size(), "model": sdk.llm_provider.model})
    except Exception as exc:  # noqa: BLE001 - same reasoning as the original: real error to the UI, not an HTML error page
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/query", methods=["POST"])
def api_query():
    # Same two branches, same response shape, as the original api_query() -
    # the frontend (an unmodified copy of the original's static/index.html)
    # doesn't know or care that this is going through an SDK object now.
    try:
        data = request.get_json(force=True)
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"error": "Incident text is required"}), 400

        # Same toggle, same default, as the original - missing/omitted means
        # today's human-in-the-loop behavior.
        human_in_loop = data.get("human_in_loop", True)

        if not human_in_loop:
            # This app cannot run a remedy script, from here or anywhere -
            # sdk.raise_remedy_candidate() only ever matches and, at most,
            # opens a real ServiceNow incident for AIOps to review in a
            # separate app (aiops_console_app/). Nothing in this file calls
            # sdk.run_confirmed_remedy_for_candidate() or sdk.auto_remediate() -
            # both exist in aiops_sdk, but this route never reaches them.
            result = sdk.raise_remedy_candidate(text)

            if result["decision"] == "raised_for_aiops_review":
                return jsonify({"mode": "raised", **result})

            # decision == "escalated": nothing confidently matched, so there
            # was nothing to hand to AIOps either - same fallback the
            # original app already has for this case.
            return jsonify({"mode": "auto", "outcome": "escalated", **{
                k: v for k, v in result.items() if k != "decision"
            }})

        # sdk.suggest() is find_similar() + build_prompt() + generate() as
        # one call - the exact two retrieval steps + one generation step the
        # original app.py runs back to back on this path.
        result = sdk.suggest(text)
        matches = result["matches"]
        suggestion = result["suggestion"]
        slm_pick = sdk.search.parse_slm_pick(suggestion, matches)

        return jsonify({"mode": "review", "matches": matches, "suggestion": suggestion, "slm_pick": slm_pick})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@app.route("/api/verdict", methods=["POST"])
def api_verdict():
    # Same three branches (confirmed / corrected / none_apply) as the
    # original api_verdict(), via sdk.search.log_feedback() and
    # sdk.servicenow.escalate() instead of separate log_feedback() /
    # escalate_to_developer() imports.
    try:
        data = request.get_json(force=True)
        text = data["text"]
        matches = data["matches"]
        slm_pick = data["slm_pick"]
        verdict_type = data["verdict"]  # "confirmed" | "corrected" | "none_apply"
        chosen_id = data.get("chosen_id")

        if verdict_type == "confirmed":
            verdict = {"verdict": "confirmed", "chosen_id": slm_pick, "chosen_text": None}
        elif verdict_type == "corrected":
            chosen = next(m for m in matches if m["incident_id"] == chosen_id)
            verdict = {"verdict": "corrected", "chosen_id": chosen_id, "chosen_text": chosen["text"]}
        else:
            verdict = {"verdict": "none_apply", "chosen_id": None, "chosen_text": None}

        sdk.search.log_feedback(text, matches, slm_pick, verdict)

        if verdict["verdict"] == "confirmed":
            return jsonify({"result": "confirmed", "final_id": slm_pick})

        if verdict["verdict"] == "corrected":
            return jsonify(
                {
                    "result": "corrected",
                    "final_id": verdict["chosen_id"],
                    "final_text": verdict["chosen_text"],
                }
            )

        incident = sdk.servicenow.escalate(text, matches)
        return jsonify(
            {
                "result": "escalated",
                "incident_number": incident["number"] if incident else None,
                "log_file": sdk.servicenow.escalation_log,
            }
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@app.route("/api/demo/state")
def demo_state():
    # Unwrapped, deliberately - see the module docstring's "why /demo is
    # imported, not duplicated" note. This talks to the same on-disk state
    # as the original app's own /api/demo/state.
    return jsonify(mock_systems.get_state())


@app.route("/api/demo/break", methods=["POST"])
def demo_break():
    try:
        data = request.get_json(force=True)
        issue_id = data.get("issue_id")
        state = mock_systems.break_issue(issue_id)
        return jsonify({"ok": True, "state": state})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/demo/reset", methods=["POST"])
def demo_reset():
    return jsonify({"ok": True, "state": mock_systems.reset_all()})


if __name__ == "__main__":
    # Defaults to 5002, not 5001 (the original's default) - so you can run
    # both apps locally at once if you want to show them side by side.
    port = int(os.environ.get("PORT", 5002))
    app.run(host="0.0.0.0", port=port)
