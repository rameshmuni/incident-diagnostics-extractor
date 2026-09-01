import os
import sys

from google import genai

from query_incident import (
    ask_human_verdict,
    build_prompt,
    embed_query,
    escalate_to_developer,
    load_index,
    log_feedback,
    parse_slm_pick,
    search,
)

# Same retrieval logic as query_incident.py (FAISS + the local embedding model, and the
# same human-verdict / feedback-log / escalation logic) - the only thing that's different
# is the reasoning step, which calls Gemini's API instead of a local Ollama model. Useful
# on a machine that can't run Ollama locally, at the cost of incident text leaving the
# machine for Google's API - worth knowing, same as any cloud LLM call.

GEMINI_MODEL = "gemini-3.6-flash"

_client = None


def get_client():
    # lazy + cached, same pattern as the embedding model cache in query_incident.py
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY not set. Add it to your .env file - get a free key at "
                "https://aistudio.google.com/apikey"
            )
        _client = genai.Client(api_key=api_key)
    return _client


def call_gemini(prompt, model=GEMINI_MODEL):
    client = get_client()
    response = client.models.generate_content(model=model, contents=prompt)
    return response.text


def main(new_incident_text=None):
    # entry point: identical flow to query_incident.py's main(), with call_gemini() standing
    # in for call_slm()
    if new_incident_text is None:
        new_incident_text = input("Describe the new incident: ")

    index, metadata = load_index()
    query_vector = embed_query(new_incident_text)
    matches = search(index, metadata, query_vector)

    print("\nClosest past incidents:")
    for m in matches:
        print(f"  {m['incident_id']}  (distance {m['distance']:.3f})  {m['short_description']}")

    prompt = build_prompt(new_incident_text, matches)
    print("\nAsking Gemini...\n")
    suggestion = call_gemini(prompt)
    print(suggestion)

    slm_pick = parse_slm_pick(suggestion, matches)
    verdict = ask_human_verdict(matches, slm_pick)
    log_feedback(new_incident_text, matches, slm_pick, verdict)

    if verdict["verdict"] == "confirmed":
        print(f"\nLogged as confirmed. Final answer stands as above ({slm_pick}).")
        return suggestion
    elif verdict["verdict"] == "corrected":
        print(f"\nLogged your correction. Using {verdict['chosen_id']}'s actual record instead:\n")
        print(verdict["chosen_text"])
        return verdict["chosen_text"]
    else:
        escalate_to_developer(new_incident_text, matches)
        return None


if __name__ == "__main__":
    arg_text = " ".join(sys.argv[1:]) or None
    main(arg_text)