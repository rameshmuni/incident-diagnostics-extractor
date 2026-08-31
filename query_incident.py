import sys
import re
import json
from pathlib import Path
from datetime import datetime, timezone

import faiss
import numpy as np
import requests
from sentence_transformers import SentenceTransformer

from seed_resolved_incidents import BASE_URL, AUTH, HEADERS, _check, _parse_json, get_caller_sys_id

INDEX_FILE = "incident_index.faiss"
METADATA_FILE = "incident_index_metadata.json"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
OLLAMA_URL = "http://localhost:11434/api/generate"
SLM_MODEL = "llama3.2"
TOP_K = 3
FEEDBACK_LOG = "feedback_log.jsonl"
ESCALATION_LOG = "escalated_to_developer.jsonl"


def load_index():
    # the index and its matching metadata were both written by build_faiss_index.py
    index = faiss.read_index(INDEX_FILE)
    metadata = json.loads(Path(METADATA_FILE).read_text())
    return index, metadata


_embedding_model_cache = None


def embed_query(text, model_name=EMBEDDING_MODEL):
    # same model used to build the index - queries and corpus have to share an embedding space.
    # cached at module level so a long-running process (the web UI) only loads it once instead
    # of on every single query - the CLI still only ever needs it loaded once per run anyway
    global _embedding_model_cache
    if _embedding_model_cache is None:
        _embedding_model_cache = SentenceTransformer(model_name)
    vector = _embedding_model_cache.encode([text], convert_to_numpy=True)
    return vector.astype(np.float32)


def search(index, metadata, query_vector, k=TOP_K):
    # FAISS only returns positions and distances - metadata[i] gives back the real record
    distances, positions = index.search(query_vector, k)
    return [
        {**metadata[pos], "distance": float(dist)}
        for pos, dist in zip(positions[0], distances[0])
        if pos != -1
    ]


def build_prompt(new_incident_text, matches):
    # this is the "augmented" step - the retrieved RCAs get pasted straight into the prompt
    context = "\n\n---\n\n".join(
        f"Past incident {m['incident_id']}:\n{m['text']}" for m in matches
    )
    return (
        "You are an incident diagnostic assistant. A new incident has come in. Below are "
        "several past resolved incidents retrieved because they are similar - they are NOT "
        "ranked by relevance, so weigh all of them on their own merits rather than defaulting "
        "to the first one. First, briefly state which single past incident's actual resolution "
        "notes best match the new incident's specific described behavior, and why. Then give "
        "the suggested root cause and resolution based on that one. If none of them are "
        "actually relevant, say so plainly instead of forcing a match.\n\n"
        f"New incident:\n{new_incident_text}\n\n"
        f"Past resolved incidents:\n{context}\n\n"
        "Which past incident is the best match, and why:"
    )


def call_slm(prompt, model=SLM_MODEL):
    # Ollama runs locally on 11434 - stream=False waits for the full response in one shot
    resp = requests.post(OLLAMA_URL, json={"model": model, "prompt": prompt, "stream": False}, timeout=120)
    resp.raise_for_status()
    return resp.json()["response"]


def parse_slm_pick(suggestion, matches):
    # pull the first INCxxxxxxx mentioned in the SLM's own text - best-effort, not guaranteed
    found = re.search(r"INC\d+", suggestion)
    if found and any(found.group() == m["incident_id"] for m in matches):
        return found.group()
    return matches[0]["incident_id"]  # fall back to the top FAISS match if we can't parse one out


def ask_human_verdict(matches, slm_pick):
    # this is the human-in-the-loop gate - nothing here is final until a person says so
    answer = input(f"\nIs {slm_pick} the correct match, and is the suggestion above right? [y/n]: ").strip().lower()
    if answer == "y":
        return {"verdict": "confirmed", "chosen_id": slm_pick, "chosen_text": None}

    print("\nWhich past incident is actually the best match?")
    for i, m in enumerate(matches, start=1):
        print(f"  {i}. {m['incident_id']}  {m['short_description']}")
    print("  0. None of these apply")
    choice = input("Enter a number: ").strip()

    if choice == "0":
        return {"verdict": "none_apply", "chosen_id": None, "chosen_text": None}
    chosen = matches[int(choice) - 1]
    return {"verdict": "corrected", "chosen_id": chosen["incident_id"], "chosen_text": chosen["text"]}


def log_feedback(new_incident_text, matches, slm_pick, verdict, log_file=FEEDBACK_LOG):
    # append-only judgment log - this is the record a future confidence/auto-remediation decision would train on
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "new_incident_text": new_incident_text,
        "retrieved_ids": [m["incident_id"] for m in matches],
        "slm_pick": slm_pick,
        "human_verdict": verdict["verdict"],
        "human_chosen_id": verdict["chosen_id"],
    }
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


def create_escalation_incident(new_incident_text):
    # opens a real ServiceNow ticket for the team's queue - no state is set, so it lands as
    # New, same as any incident a person would have logged by hand
    caller_sys_id = get_caller_sys_id()
    payload = {
        "short_description": new_incident_text[:160],  # ServiceNow truncates long titles anyway
        "description": (
            "Auto-logged by the RAG assistant: no past resolved incident was a confident match "
            "for this one, so it needs a developer to investigate and resolve from scratch.\n\n"
            f"{new_incident_text}"
        ),
        "category": "Software",
        "priority": "3",
        "caller_id": caller_sys_id,
    }
    resp = _check(requests.post(f"{BASE_URL}/table/incident", auth=AUTH, headers=HEADERS, timeout=30, json=payload))
    return _parse_json(resp)["result"]


def escalate_to_developer(new_incident_text, matches, log_file=ESCALATION_LOG):
    # nothing in the corpus applies - open a real ticket so a human solves it fresh in ServiceNow
    # itself (not just a local log), the same way any brand-new problem would be worked
    incident = create_escalation_incident(new_incident_text)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "new_incident_text": new_incident_text,
        "retrieved_but_rejected_ids": [m["incident_id"] for m in matches],
        "status": "unresolved_by_ai",
        "servicenow_incident": incident["number"],
        "servicenow_sys_id": incident["sys_id"],
    }
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(
        f"\nNo confident match - opened {incident['number']} in ServiceNow (state: New) for "
        f"developer investigation (also logged to {log_file}).\n"
        "Once the team resolves it there with a proper Root Cause + Resolution in the close notes, "
        "re-run fetch_resolved_incidents.py then build_faiss_index.py to fold it into the corpus - "
        "the next time this same issue comes in, it'll be retrieved and suggested automatically."
    )
    return incident


def main(new_incident_text=None):
    # entry point: embed the new incident, retrieve similar past ones, ask the SLM to reason, then check with a human
    if new_incident_text is None:
        new_incident_text = input("Describe the new incident: ")

    index, metadata = load_index()
    query_vector = embed_query(new_incident_text)
    matches = search(index, metadata, query_vector)

    print("\nClosest past incidents:")
    for m in matches:
        print(f"  {m['incident_id']}  (distance {m['distance']:.3f})  {m['short_description']}")

    prompt = build_prompt(new_incident_text, matches)
    print("\nAsking the SLM...\n")
    suggestion = call_slm(prompt)
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