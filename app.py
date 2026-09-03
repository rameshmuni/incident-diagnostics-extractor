import os

from flask import Flask, jsonify, request, send_from_directory

from query_incident import (
    ESCALATION_LOG,
    build_prompt,
    embed_query,
    escalate_to_developer,
    load_index,
    log_feedback,
    parse_slm_pick,
    search,
)
from query_incident_gemini import GEMINI_MODEL, call_gemini

# serves static/index.html at "/" and everything else in static/ at its own path -
# this app is just a thin HTTP wrapper around the exact same retrieval/logging functions
# query_incident.py's CLI uses, with call_gemini() (Gemini API, free tier) standing in for
# call_slm() (Ollama) as the reasoning step - this is the Cloud Run deployment target, so it
# calls the hosted API rather than self-hosting a model: Cloud Run's always-free tier is
# CPU-only, and self-hosting even a small model (Qwen) at usable latency needs paid GPU.
# query_incident_qwen.py stays around as the local/office-laptop-only path, not used here.
app = Flask(__name__, static_folder="static", static_url_path="")


@app.route("/")
def home():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/status")
def status():
    # lets the UI show a live "connected" strip instead of hardcoding the corpus size
    try:
        index, _metadata = load_index()
        return jsonify({"ok": True, "corpus_size": index.ntotal, "model": GEMINI_MODEL})
    except Exception as exc:  # noqa: BLE001 - surfacing any startup issue to the UI is the point
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/query", methods=["POST"])
def api_query():
    # same retrieval + generation steps as main() in query_incident.py, minus the blocking
    # input() call - the human verdict happens as a separate request once the UI has rendered
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "Incident text is required"}), 400

    index, metadata = load_index()
    query_vector = embed_query(text)
    matches = search(index, metadata, query_vector)

    prompt = build_prompt(text, matches)
    suggestion = call_gemini(prompt)
    slm_pick = parse_slm_pick(suggestion, matches)

    return jsonify({"matches": matches, "suggestion": suggestion, "slm_pick": slm_pick})


@app.route("/api/verdict", methods=["POST"])
def api_verdict():
    # same three branches as ask_human_verdict()/main() in query_incident.py, driven by a
    # button click in the UI instead of a y/n + number prompt in the terminal
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


if __name__ == "__main__":
    # Cloud Run injects the PORT env var and expects the container to listen on 0.0.0.0 -
    # this fallback to 5001 with debug off keeps local testing possible too, but the real
    # entrypoint in the container is gunicorn (see Dockerfile), not this block
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)