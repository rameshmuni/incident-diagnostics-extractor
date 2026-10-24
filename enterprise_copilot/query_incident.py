"""
The core flow: describe an incident in plain text, get back the closest
matches from the knowledge base (Confluence + ServiceNow, whatever
ingest_confluence.py / ingest_servicenow.py have loaded) plus a suggested
root cause - then a human confirms, corrects, or rejects it before anything
is treated as final.

Run it interactively from a terminal:
    python query_incident.py

Or import suggest()/log_feedback() from your own script or a small web app -
nothing in here depends on being run as a CLI.
"""
import json
from datetime import datetime, timezone

import embeddings_daisy as embedder
import llm_daisy as llm
import vectorstore_chroma as store
import servicenow_client as sn

FEEDBACK_LOG = "feedback_log.jsonl"

SYSTEM_PROMPT = (
    "You are an incident diagnostic assistant for an internal RPA operations team. "
    "You will be given a new incident description and several retrieved past incidents "
    "or knowledge-base articles that may or may not be relevant. Weigh each one on its "
    "own merits - they are not pre-ranked by relevance. First, briefly state which single "
    "retrieved item best matches the new incident's specific described behavior, and why. "
    "Then give the suggested root cause and resolution based on that one. If none of them "
    "are actually relevant, say so plainly instead of forcing a match."
)


def search_similar(incident_text, k=None):
    """Embed the incident text and return the k closest knowledge-base
    records (see vectorstore_chroma.search_similar for the shape)."""
    query_vector = embedder.embed_text(incident_text)
    return store.search_similar(query_vector, k=k)


def build_prompt(new_incident_text, matches):
    context = "\n\n---\n\n".join(
        f"Retrieved item {i + 1} (source: {m['metadata'].get('source')}, id: {m['id']}):\n{m['text']}"
        for i, m in enumerate(matches)
    )
    return (
        f"New incident:\n{new_incident_text}\n\n"
        f"Retrieved items:\n{context}\n\n"
        "Which retrieved item is the best match, and why:"
    )


def parse_pick(suggestion, matches):
    """Best-effort: pull the first retrieved item id the model actually
    named in its own text. Falls back to the closest-distance match if it
    didn't clearly name one - matches query_incident.py's original
    parse_slm_pick() behavior from the GCP demo project."""
    for m in matches:
        if m["id"] in suggestion:
            return m["id"]
    return matches[0]["id"] if matches else None


def get_learned_override(new_incident_text, matches):
    """Reuse a prior human correction for this *exact* incident text, if
    one exists in the feedback log and the chosen item is still among
    today's retrieved matches. Without this, a correction would only ever
    get logged, never read back - so the same wrong pick could resurface
    every time the same question is re-asked. Exact-text match only
    (case/whitespace-insensitive); a paraphrased re-ask still goes through
    normal retrieval + generation."""
    normalized = new_incident_text.strip().lower()
    retrieved_ids = {m["id"] for m in matches}
    best = None  # feedback log is append-only in order, so the last match wins
    try:
        with open(FEEDBACK_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("new_incident_text", "").strip().lower() != normalized:
                    continue
                if entry.get("human_verdict") not in ("corrected", "confirmed"):
                    continue
                chosen = entry.get("human_chosen_id")
                if not chosen or chosen not in retrieved_ids:
                    continue  # no longer one of today's retrieved matches - don't force a stale pick
                best = chosen
    except FileNotFoundError:
        return None
    return best


def suggest(incident_text, k=None):
    """The main entry point. Returns {"matches": [...], "suggestion": str,
    "pick": id_or_None}. Checks for a learned override before calling the
    LLM - see get_learned_override()."""
    matches = search_similar(incident_text, k=k)
    if not matches:
        return {
            "matches": [],
            "suggestion": "No knowledge-base entries found at all - is Chroma empty? "
                           "Run ingest_confluence.py and/or ingest_servicenow.py first.",
            "pick": None,
        }

    override_id = get_learned_override(incident_text, matches)
    if override_id:
        chosen = next(m for m in matches if m["id"] == override_id)
        suggestion = (
            f"A reviewer previously confirmed this exact issue matches {override_id} - "
            f"reusing that verdict instead of asking again.\n\n{chosen['text']}"
        )
        pick = override_id
    else:
        prompt = build_prompt(incident_text, matches)
        suggestion = llm.chat_completion(prompt, system_prompt=SYSTEM_PROMPT)
        pick = parse_pick(suggestion, matches)

    return {"matches": matches, "suggestion": suggestion, "pick": pick}


def log_feedback(new_incident_text, matches, pick, verdict):
    """verdict is {"verdict": "confirmed"|"corrected"|"none_apply", "chosen_id": id_or_None}."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "new_incident_text": new_incident_text,
        "retrieved_ids": [m["id"] for m in matches],
        "pick": pick,
        "human_verdict": verdict["verdict"],
        "human_chosen_id": verdict["chosen_id"],
    }
    with open(FEEDBACK_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def ask_human_verdict(matches, pick):
    """The human-in-the-loop gate for the CLI - nothing is final until a
    person says so. A web app would replace this one function with its own
    UI and call log_feedback() the same way."""
    answer = input(f"\nIs {pick} the correct match, and is the suggestion above right? [y/n]: ").strip().lower()
    if answer == "y":
        return {"verdict": "confirmed", "chosen_id": pick}

    print("\nWhich retrieved item is actually the best match?")
    for i, m in enumerate(matches, start=1):
        print(f"  {i}. {m['id']}  (source: {m['metadata'].get('source')})  {m['text'][:80]}")
    print("  0. None of these apply")
    choice = input("Enter a number: ").strip()

    if choice == "0":
        return {"verdict": "none_apply", "chosen_id": None}
    chosen = matches[int(choice) - 1]
    return {"verdict": "corrected", "chosen_id": chosen["id"]}


def main():
    incident_text = input("Describe the new incident: ").strip()
    if not incident_text:
        print("Nothing entered.")
        return

    result = suggest(incident_text)
    matches = result["matches"]
    if not matches:
        print(result["suggestion"])
        return

    print("\nClosest matches:")
    for m in matches:
        print(f"  {m['id']}  (distance {m['distance']:.4f}, source: {m['metadata'].get('source')})")

    print(f"\nSuggestion:\n{result['suggestion']}")

    verdict = ask_human_verdict(matches, result["pick"])
    log_feedback(incident_text, matches, result["pick"], verdict)

    if verdict["verdict"] == "none_apply":
        incident = sn.escalate(incident_text)
        print(f"\nNo confident match - opened {incident['number']} in ServiceNow (state: New) for a developer.")
    else:
        print("\nLogged. Thanks!")


if __name__ == "__main__":
    main()
