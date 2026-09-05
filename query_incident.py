import sys
import re
import json
import os
import time
from pathlib import Path
from datetime import datetime, timezone

import requests
from google import genai
from google.genai import types as genai_types
from google.cloud import bigquery

from seed_resolved_incidents import BASE_URL, AUTH, HEADERS, _check, _parse_json, get_caller_sys_id

# Google-native retrieval: Gemini's embedding model stands in for sentence-transformers,
# BigQuery's VECTOR_SEARCH stands in for FAISS. See learning_plan_gcp_native.md for the
# full reasoning behind every choice below - this file is the code side of that plan.
EMBEDDING_MODEL = "gemini-embedding-2"
# Gemini Embedding 2 defaults to 3072 dims but is explicitly designed to be truncated (and
# auto-normalizes at the truncated size too) - 768 keeps BigQuery storage/scan costs small
# without giving up meaningful retrieval quality at this corpus size.
EMBEDDING_DIMENSIONS = 768
BQ_DATASET = os.environ.get("BQ_DATASET", "incident_assistant")
BQ_TABLE = os.environ.get("BQ_TABLE", "incident_corpus")
OLLAMA_URL = "http://localhost:11434/api/generate"
SLM_MODEL = "llama3.2"
TOP_K = 3
FEEDBACK_LOG = "feedback_log.jsonl"
ESCALATION_LOG = "escalated_to_developer.jsonl"

_genai_client_cache = None
_bq_client_cache = None


def get_genai_client():
    # lazy + cached, same pattern the old embedding-model cache used - only built once per
    # process, whether that process is this CLI or the long-running web service
    global _genai_client_cache
    if _genai_client_cache is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY not set. Add it to your .env file locally, or to Secret "
                "Manager on Cloud Run - get a free key at https://aistudio.google.com/apikey"
            )
        _genai_client_cache = genai.Client(api_key=api_key)
    return _genai_client_cache


def get_bq_client():
    # Cloud Run's runtime service account supplies Application Default Credentials
    # automatically, so bigquery.Client() just works with no key file and no explicit
    # project id - same as running `bq query` on your laptop after
    # `gcloud auth application-default login`
    global _bq_client_cache
    if _bq_client_cache is None:
        _bq_client_cache = bigquery.Client()
    return _bq_client_cache


def table_ref(client=None):
    client = client or get_bq_client()
    return f"{client.project}.{BQ_DATASET}.{BQ_TABLE}"


EMBED_MAX_RETRIES = 5
EMBED_INITIAL_BACKOFF_SECONDS = 4


def _embed_with_retry(contents, task_type, model=EMBEDDING_MODEL):
    # Every embed_content() call - even one with a single string - goes over the same
    # batchEmbedContents transport under the hood in this SDK, and that endpoint's rate
    # limit is tighter than you'd expect from calling it a lot in a tight loop (this is a
    # known rough edge - see googleapis/python-genai#427). A single 429 shouldn't take down
    # the whole refresh job, so this retries with exponential backoff before giving up.
    client = get_genai_client()
    config = genai_types.EmbedContentConfig(
        task_type=task_type,
        output_dimensionality=EMBEDDING_DIMENSIONS,
    )
    delay = EMBED_INITIAL_BACKOFF_SECONDS
    for attempt in range(1, EMBED_MAX_RETRIES + 1):
        try:
            return client.models.embed_content(model=model, contents=contents, config=config)
        except Exception as exc:  # noqa: BLE001 - only rate-limit errors get retried, everything else re-raises
            is_rate_limited = "RESOURCE_EXHAUSTED" in str(exc) or "429" in str(exc)
            if not is_rate_limited or attempt == EMBED_MAX_RETRIES:
                raise
            print(f"Embedding rate-limited (attempt {attempt}/{EMBED_MAX_RETRIES}), waiting {delay}s...")
            time.sleep(delay)
            delay *= 2


def embed_text(text, task_type, model=EMBEDDING_MODEL):
    # task_type is asymmetric on purpose: a resolved incident already sitting in the corpus
    # is embedded as something that will be SEARCHED FOR (RETRIEVAL_DOCUMENT), while a brand
    # new incident is embedded as something DOING the searching (RETRIEVAL_QUERY). Gemini's
    # embedding model produces measurably better matches when it knows which side of the
    # search each piece of text is on, instead of treating both identically the way a single
    # generic embed() call (what sentence-transformers did) would.
    response = _embed_with_retry(text, task_type, model=model)
    return response.embeddings[0].values


def embed_texts_batch(texts, task_type, model=EMBEDDING_MODEL):
    # the corpus-building equivalent of embed_text() - one API call embeds a whole chunk of
    # records at once (Gemini's embed_content accepts a list of strings and returns one
    # embedding per string, in order), instead of one call per record. This is the actual
    # fix for the 429s: 102 records as ~5 calls of 20 each is far less likely to trip a
    # rate limit than 102 individual calls fired back to back ever was.
    if not texts:
        return []
    response = _embed_with_retry(texts, task_type, model=model)
    return [e.values for e in response.embeddings]


def embed_query(text):
    # the query-side embedding of a new incident - the direct replacement for the old
    # embed_query() that ran SentenceTransformer.encode() locally
    return embed_text(text, task_type="RETRIEVAL_QUERY")


def search(query_vector, k=TOP_K):
    # the direct replacement for index.search(query_vector, k) - BigQuery does the nearest-
    # neighbor math instead of FAISS, and hands back the real record columns directly (no
    # separate metadata.json needed - the table row *is* the metadata now)
    client = get_bq_client()
    query = f"""
        SELECT
          base.incident_id AS incident_id,
          base.sys_id AS sys_id,
          base.short_description AS short_description,
          base.category AS category,
          base.text AS text,
          distance
        FROM VECTOR_SEARCH(
            TABLE `{table_ref(client)}`,
            'embedding',
            (SELECT @query_vector AS embedding),
            top_k => @k,
            distance_type => 'COSINE',
            options => '{{"use_brute_force":true}}'
        )
        ORDER BY distance ASC
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("query_vector", "FLOAT64", query_vector),
        bigquery.ScalarQueryParameter("k", "INT64", k),
    ])
    return [
        {**dict(row), "distance": float(row["distance"])}
        for row in client.query(query, job_config=job_config).result()
    ]


def corpus_size():
    # replacement for the old index.ntotal - lets /api/status report a live count without
    # keeping any local index object around at all
    client = get_bq_client()
    query = f"SELECT COUNT(*) AS n FROM `{table_ref(client)}`"
    rows = list(client.query(query).result())
    return rows[0]["n"] if rows else 0


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
    # Ollama runs locally on 11434 - stream=False waits for the full response in one shot.
    # Kept around for local/offline reasoning during development; not used on Cloud Run
    # (see query_incident_gemini.py / app.py, which use call_gemini() instead). Note that
    # retrieval itself now needs the network either way (Gemini embeddings + BigQuery), so
    # this path saves a Gemini generation call, not a fully offline run.
    resp = requests.post(OLLAMA_URL, json={"model": model, "prompt": prompt, "stream": False}, timeout=120)
    resp.raise_for_status()
    return resp.json()["response"]


def parse_slm_pick(suggestion, matches):
    # pull the first INCxxxxxxx mentioned in the SLM's own text - best-effort, not guaranteed
    found = re.search(r"INC\d+", suggestion)
    if found and any(found.group() == m["incident_id"] for m in matches):
        return found.group()
    return matches[0]["incident_id"]  # fall back to the top match if we can't parse one out


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
        "re-run fetch_resolved_incidents.py then build_bigquery_corpus.py to fold it into the "
        "corpus - the next time this same issue comes in, it'll be retrieved and suggested "
        "automatically."
    )
    return incident


def main(new_incident_text=None):
    # entry point: embed the new incident, retrieve similar past ones, ask the SLM to reason, then check with a human
    if new_incident_text is None:
        new_incident_text = input("Describe the new incident: ")

    query_vector = embed_query(new_incident_text)
    matches = search(query_vector)

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
