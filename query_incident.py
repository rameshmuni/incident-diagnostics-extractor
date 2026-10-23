import sys
import re
import json
import math
import os
import time
from pathlib import Path
from datetime import datetime, timezone

import requests
from google import genai
from google.genai import types as genai_types
from google.cloud import bigquery

from seed_resolved_incidents import BASE_URL, AUTH, HEADERS, _check, _parse_json, get_caller_sys_id, request_with_retry

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
# Durable home for human verdicts (confirm/correct/none-apply) - see log_feedback() and
# get_learned_override() below. Feedback used to live only in feedback_log.jsonl on local
# disk, which doesn't survive a Cloud Run redeploy or scale-to-zero - a correction could
# silently stop sticking once the instance that logged it recycled. Same dataset as the
# corpus, separate table, since the two have very different write patterns (corpus:
# truncate-and-reload in bulk; feedback: append one row at a time, forever).
BQ_FEEDBACK_TABLE = os.environ.get("BQ_FEEDBACK_TABLE", "incident_feedback")
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


FEEDBACK_SCHEMA = [
    bigquery.SchemaField("timestamp", "TIMESTAMP"),
    bigquery.SchemaField("new_incident_text", "STRING"),
    bigquery.SchemaField("retrieved_ids", "STRING", mode="REPEATED"),
    bigquery.SchemaField("slm_pick", "STRING"),
    bigquery.SchemaField("human_verdict", "STRING"),
    bigquery.SchemaField("human_chosen_id", "STRING"),
]

_feedback_table_ensured = False


def feedback_table_ref(client=None):
    client = client or get_bq_client()
    return f"{client.project}.{BQ_DATASET}.{BQ_FEEDBACK_TABLE}"


def ensure_feedback_table(client=None):
    # idempotent, cached per-process so a normal request doesn't pay for the exists_ok
    # metadata round-trip every time - only the first call after a cold start does
    global _feedback_table_ensured
    if _feedback_table_ensured:
        return
    client = client or get_bq_client()
    client.create_dataset(bigquery.DatasetReference(client.project, BQ_DATASET), exists_ok=True)
    client.create_table(bigquery.Table(feedback_table_ref(client), schema=FEEDBACK_SCHEMA), exists_ok=True)
    _feedback_table_ensured = True


EMBED_MAX_RETRIES = 5
EMBED_INITIAL_BACKOFF_SECONDS = 4


def _embed_with_retry(text, task_type, model=EMBEDDING_MODEL):
    # One text per call, on purpose - see embed_text()'s comment below for why passing
    # multiple texts to gemini-embedding-2 in one call turned out to silently drop almost
    # all of them instead of erroring. A single 429 (or a bad/empty response) shouldn't
    # take down the whole refresh job, so this retries with exponential backoff first.
    client = get_genai_client()
    config = genai_types.EmbedContentConfig(
        task_type=task_type,
        output_dimensionality=EMBEDDING_DIMENSIONS,
    )
    delay = EMBED_INITIAL_BACKOFF_SECONDS
    for attempt in range(1, EMBED_MAX_RETRIES + 1):
        try:
            response = client.models.embed_content(model=model, contents=text, config=config)
            values = response.embeddings[0].values
            if not values:
                # Seen in practice: no exception raised, but the returned embedding is
                # empty. Treat that as a failure worth retrying, same as a 429 - the
                # alternative is silently writing a useless all-empty vector into BigQuery.
                raise RuntimeError("embed_content returned an empty embedding")
            return values
        except Exception as exc:  # noqa: BLE001 - only retryable cases get retried, everything else re-raises
            # RESOURCE_EXHAUSTED/429 is rate-limiting; UNAVAILABLE/503 is Gemini being
            # temporarily overloaded ("This model is currently experiencing high demand...")
            # - both are worth retrying for the same reason, neither means our request was
            # wrong.
            is_retryable = (
                "RESOURCE_EXHAUSTED" in str(exc)
                or "429" in str(exc)
                or "UNAVAILABLE" in str(exc)
                or "503" in str(exc)
                or "empty embedding" in str(exc)
            )
            if not is_retryable or attempt == EMBED_MAX_RETRIES:
                raise
            print(f"Embedding attempt {attempt}/{EMBED_MAX_RETRIES} failed ({exc}), waiting {delay}s...")
            time.sleep(delay)
            delay *= 2


def _fit_dimensions(vector, dimensions=EMBEDDING_DIMENSIONS):
    # Defensive only at this point - output_dimensionality in the config above should
    # already produce exactly `dimensions` values, but this guarantees it (and re-
    # normalizes to unit length, which Gemini's embedding models require after any
    # truncation) rather than trusting that to always hold.
    if len(vector) <= dimensions:
        return vector
    truncated = vector[:dimensions]
    norm = math.sqrt(sum(v * v for v in truncated))
    return [v / norm for v in truncated] if norm else truncated


def embed_text(text, task_type, model=EMBEDDING_MODEL):
    # task_type is asymmetric on purpose: a resolved incident already sitting in the corpus
    # is embedded as something that will be SEARCHED FOR (RETRIEVAL_DOCUMENT), while a brand
    # new incident is embedded as something DOING the searching (RETRIEVAL_QUERY). Gemini's
    # embedding model produces measurably better matches when it knows which side of the
    # search each piece of text is on, instead of treating both identically the way a single
    # generic embed() call (what sentence-transformers did) would.
    #
    # One text per call - an earlier version of this function batched many texts into one
    # embed_content() call to cut down on API calls (Google's own docs show this as a
    # supported pattern, for an older model). For gemini-embedding-2 specifically it turned
    # out to silently return an empty embedding for almost every text in the batch instead
    # of erroring - roughly one real result per batch, no matter the batch size - which
    # went unnoticed until BigQuery refused to compare the resulting mixed-dimension
    # vectors. One call per text is slower but actually correct for this model; see
    # build_bigquery_corpus.py for how the corpus-building loop paces these calls to avoid
    # the rate-limit problem batching was originally introduced to solve.
    return _fit_dimensions(_embed_with_retry(text, task_type, model=model))


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


def _override_from_local_log(normalized, retrieved_ids, log_file):
    # Fallback path only - used when BigQuery itself is unreachable (or the table hasn't
    # been created yet). Same matching rules as the BigQuery query below: exact
    # case/whitespace-insensitive text match, verdict confirmed/corrected, chosen incident
    # still among today's retrieved matches, latest entry wins.
    if not os.path.exists(log_file):
        return None
    best = None
    with open(log_file) as f:
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
                continue
            best = chosen
    return best


def get_learned_override(new_incident_text, matches, log_file=FEEDBACK_LOG):
    """A prior human verdict on this exact incident text, if one exists.

    Without this, log_feedback() below is write-only - a "corrected" verdict
    gets recorded but nothing ever reads it back, so the SLM makes the same
    wrong pick again the next time the same question comes in. This closes
    that loop: if a reviewer previously corrected (or confirmed) the pick
    for this exact text, and that chosen incident is still among today's
    retrieved matches, reuse their answer instead of asking the LLM again.

    BigQuery is the primary, durable source (see BQ_FEEDBACK_TABLE) - it
    survives a Cloud Run redeploy or scale-to-zero, unlike the local
    feedback_log.jsonl file, which only ever reflected what this one
    instance happened to log before it recycled. If BigQuery itself can't
    be reached (or the table doesn't exist yet), this falls back to the
    local file instead of failing the whole request.

    Exact-text match only (case/whitespace-insensitive) - this is a direct
    recall of one specific past correction, not a semantic similarity
    lookup, so paraphrased re-asks still go through normal retrieval +
    generation.
    """
    normalized = new_incident_text.strip().lower()
    retrieved_ids = {m["incident_id"] for m in matches}
    if not retrieved_ids:
        return None

    try:
        client = get_bq_client()
        query = f"""
            SELECT human_chosen_id
            FROM `{feedback_table_ref(client)}`
            WHERE LOWER(TRIM(new_incident_text)) = @normalized_text
              AND human_verdict IN ('corrected', 'confirmed')
              AND human_chosen_id IN UNNEST(@retrieved_ids)
            ORDER BY timestamp DESC
            LIMIT 1
        """
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("normalized_text", "STRING", normalized),
            bigquery.ArrayQueryParameter("retrieved_ids", "STRING", list(retrieved_ids)),
        ])
        rows = list(client.query(query, job_config=job_config).result())
        return rows[0]["human_chosen_id"] if rows else None
    except Exception as exc:  # noqa: BLE001 - BigQuery unreachable or table not created yet
        print(f"[get_learned_override] BigQuery lookup failed, falling back to local log: {exc}")
        return _override_from_local_log(normalized, retrieved_ids, log_file)


def build_override_suggestion(matches, override_id):
    """The same kind of suggestion text generate() would produce, but built
    deterministically from a past human correction instead of asking the LLM
    again - so the highlighted pick and the explanation text always agree,
    and a confirmed correction can't silently drift on a later re-ask."""
    chosen = next(m for m in matches if m["incident_id"] == override_id)
    return (
        f"A reviewer previously confirmed this exact issue matches {override_id} "
        f"({chosen['short_description']}) - reusing that verdict instead of "
        f"asking again.\n\n{chosen['text']}"
    )


def log_feedback(new_incident_text, matches, slm_pick, verdict, log_file=FEEDBACK_LOG):
    # append-only judgment log - this is the record a future confidence/auto-remediation
    # decision (and get_learned_override() above) trains on
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

    # BigQuery is the durable copy get_learned_override() actually reads back - the local
    # file above lives on this Cloud Run instance's ephemeral disk only. A BQ hiccup here
    # shouldn't break the verdict response the user is waiting on, so this degrades to
    # "local-only for now" (still logged, just not durable yet) instead of raising.
    try:
        client = get_bq_client()
        ensure_feedback_table(client)
        errors = client.insert_rows_json(feedback_table_ref(client), [entry])
        if errors:
            print(f"[log_feedback] BigQuery insert reported row errors, local log still has this entry: {errors}")
    except Exception as exc:  # noqa: BLE001 - see comment above
        print(f"[log_feedback] BigQuery insert failed, local log still has this entry: {exc}")


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
    resp = _check(request_with_retry("post", f"{BASE_URL}/table/incident", json=payload))
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
