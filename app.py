import os

from flask import Flask, jsonify, request, send_from_directory

from query_incident import (
    ESCALATION_LOG,
    build_prompt,
    corpus_size,
    embed_query,
    escalate_to_developer,
    log_feedback,
    parse_slm_pick,
    search,
)
from query_incident_gemini import GEMINI_MODEL, call_gemini
from week3.auto_remediation import attempt_auto_remediation
from week3.demo_app import demo_bp
from week3 import mock_systems

# serves static/index.html at "/" and everything else in static/ at its own path -
# this app is just a thin HTTP wrapper around the exact same retrieval/logging functions
# query_incident.py's CLI uses, with call_gemini() (Gemini API, free tier) standing in for
# call_slm() (Ollama) as the reasoning step. Retrieval itself is Gemini embeddings + BigQuery
# VECTOR_SEARCH (see query_incident.py) - nothing here runs a model or holds an index in
# memory, so this container is a genuinely thin, stateless HTTP layer end to end.
# query_incident_qwen.py stays around as the local/office-laptop-only path, not used here.
app = Flask(__name__, static_folder="static", static_url_path="")

# Week 3's real demo app (login + upload pages) lives under /demo, in this exact same
# process/container - see demo_app.py's module docstring for why that has to be true.
app.register_blueprint(demo_bp)

# make sure the real uploads/ folder exists (healthy, writable) before anything can touch
# it - the Dockerfile already creates it this way in the deployed container, this is only
# needed for local runs where that build step never happened
mock_systems.ensure_upload_dir()


@app.route("/")
def home():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/status")
def status():
    # lets the UI show a live "connected" strip instead of hardcoding the corpus size -
    # corpus_size() runs a live COUNT(*) against BigQuery, so this doubles as a real
    # connectivity check for both BigQuery and (implicitly) the service account's IAM
    try:
        return jsonify({"ok": True, "corpus_size": corpus_size(), "model": GEMINI_MODEL})
    except Exception as exc:  # noqa: BLE001 - surfacing any startup issue to the UI is the point
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/query", methods=["POST"])
def api_query():
    # same retrieval + generation steps as main() in query_incident.py, minus the blocking
    # input() call - the human verdict happens as a separate request once the UI has rendered
    #
    # Everything below is wrapped in try/except on purpose: without it, any exception here
    # (a rate-limited embed call, a BigQuery error, a Gemini generation error) falls through
    # to Flask's default error handler, which returns an HTML error page - and the frontend's
    # `await resp.json()` then throws its own confusing "Unexpected token '<'" parse error on
    # top of whatever actually went wrong, hiding the real cause. Returning JSON here even on
    # failure means the UI shows the actual error message instead of that generic one.
    try:
        data = request.get_json(force=True)
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"error": "Incident text is required"}), 400

        # Week 3's toggle. Default True (missing/omitted = today's Week 2 behavior, so any
        # older client that doesn't send this field at all keeps working unchanged) - only an
        # explicit `false` skips the human verdict step and lets the bot act on its own.
        human_in_loop = data.get("human_in_loop", True)

        if not human_in_loop:
            # No retrieval-and-suggest step at all on this path - attempt_auto_remediation()
            # either runs a known remedy and resolves the incident, or escalates a new one.
            # Either way it's already final by the time it returns; there's nothing for a
            # human to confirm, so /api/verdict is never called for this response.
            result = attempt_auto_remediation(text)
            return jsonify({"mode": "auto", **result})

        query_vector = embed_query(text)
        matches = search(query_vector)

        prompt = build_prompt(text, matches)
        suggestion = call_gemini(prompt)
        slm_pick = parse_slm_pick(suggestion, matches)

        return jsonify({"mode": "review", "matches": matches, "suggestion": suggestion, "slm_pick": slm_pick})
    except Exception as exc:  # noqa: BLE001 - surfacing the real error to the UI is the point
        return jsonify({"error": str(exc)}), 500


@app.route("/api/verdict", methods=["POST"])
def api_verdict():
    # same three branches as ask_human_verdict()/main() in query_incident.py, driven by a
    # button click in the UI instead of a y/n + number prompt in the terminal.
    # Wrapped for the same reason as api_query() above - a failed ServiceNow escalation
    # call, for instance, should come back as a readable JSON error, not an HTML error page.
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

        log_feedback(text, matches, slm_pick, verdict)

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

        incident = escalate_to_developer(text, matches)
        return jsonify(
            {
                "result": "escalated",
                "incident_number": incident["number"] if incident else None,
                "log_file": ESCALATION_LOG,
            }
        )
    except Exception as exc:  # noqa: BLE001 - surfacing the real error to the UI is the point
        return jsonify({"error": str(exc)}), 500


@app.route("/api/demo/state")
def demo_state():
    # read-only view of the mock systems' current state - lets the UI (or you, via curl)
    # confirm what's actually broken before running a query, without guessing
    return jsonify(mock_systems.get_state())


@app.route("/api/demo/break", methods=["POST"])
def demo_break():
    # Cloud Run's local disk isn't shared with Cloud Shell or anywhere else - this has to be
    # an API call against the *running service* itself, not a CLI script run somewhere else,
    # or "break" and "fix" would end up touching two different filesystems entirely. Calling
    # mock_systems.break_issue() here is what keeps the demo's before/after state consistent.
    #
    # Deliberately does NOT touch ServiceNow at all - the demo app only breaks a real thing,
    # nothing more. Raising an incident is a human's job: they notice the app is broken and
    # report it through the chat (human_in_loop off), which is what actually creates and
    # resolves the incident - see attempt_auto_remediation() in auto_remediation.py. Keeping
    # this route ServiceNow-free means the demo app genuinely doesn't know ServiceNow exists.
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
    # Cloud Run injects the PORT env var and expects the container to listen on 0.0.0.0 -
    # this fallback to 5001 with debug off keeps local testing possible too, but the real
    # entrypoint in the container is gunicorn (see Dockerfile), not this block
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)
