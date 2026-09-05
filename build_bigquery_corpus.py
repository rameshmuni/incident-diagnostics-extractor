import json
import time
from pathlib import Path

from google.cloud import bigquery

from query_incident import BQ_DATASET, embed_text, get_bq_client, table_ref

CORPUS_FILE = "resolved_incidents_corpus.json"
# One embed_content() call per record, paced with a small delay between calls - this is
# the actual fix for the original 429 RESOURCE_EXHAUSTED problem. An earlier version of
# this file batched many records into one call instead, which avoided the 429s but turned
# out to silently return empty embeddings for almost every record in a batch (a real
# limitation of gemini-embedding-2's batch support, not something a delay would help with)
# - see embed_text()'s comment in query_incident.py for the full story. A per-record pause
# is the one lever left to avoid tripping the rate limit without touching batch size.
SECONDS_BETWEEN_CALLS = 1.5

# the BigQuery equivalent of incident_index_metadata.json's shape, plus the one column
# FAISS used to keep separately (in the .faiss file itself): the embedding.
SCHEMA = [
    bigquery.SchemaField("incident_id", "STRING"),
    bigquery.SchemaField("sys_id", "STRING"),
    bigquery.SchemaField("short_description", "STRING"),
    bigquery.SchemaField("category", "STRING"),
    bigquery.SchemaField("text", "STRING"),
    bigquery.SchemaField("embedding", "FLOAT64", mode="REPEATED"),  # ARRAY<FLOAT64> in SQL
]


def load_corpus(path=CORPUS_FILE):
    # the flat records fetch_resolved_incidents.py already prepared
    return json.loads(Path(path).read_text())


def embed_records(records):
    # RETRIEVAL_DOCUMENT: these are the "answer side" of the search, not the "question side" -
    # see embed_text()'s docstring in query_incident.py for why the corpus and an incoming
    # query get different task_type values even though they go through the same model.
    for i, record in enumerate(records, start=1):
        record["embedding"] = embed_text(record["text"], task_type="RETRIEVAL_DOCUMENT")
        if i % 10 == 0 or i == len(records):
            print(f"Embedded {i}/{len(records)} record(s)...")
        if i < len(records):
            time.sleep(SECONDS_BETWEEN_CALLS)
    return records


def load_to_bigquery(records):
    client = get_bq_client()

    # BigQuery datasets aren't auto-created the way tables can be - this is a one-time no-op
    # after the first run (exists_ok=True), same as `bq mk --dataset` would do by hand
    client.create_dataset(bigquery.DatasetReference(client.project, BQ_DATASET), exists_ok=True)

    job_config = bigquery.LoadJobConfig(
        schema=SCHEMA,
        # WRITE_TRUNCATE + CREATE_IF_NEEDED (the default) = one atomic "replace the whole
        # corpus with whatever's currently resolved in ServiceNow" - the exact same
        # rebuild-from-scratch semantics build_faiss_index.py always had, just against a
        # table instead of a local .faiss file, and the table is created automatically the
        # very first time this runs.
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    table_id = table_ref(client)
    job = client.load_table_from_json(records, table_id, job_config=job_config)
    job.result()  # block until the load job actually finishes - matters for run_refresh.py,
    # which needs this step done before it can call it "refresh complete"
    return table_id


def main():
    # entry point: embed the corpus once, load it into BigQuery, replacing whatever was there
    records = load_corpus()
    records = embed_records(records)
    table_id = load_to_bigquery(records)

    print(f"Indexed {len(records)} record(s), dimension {len(records[0]['embedding']) if records else 0}")
    print(f"Loaded into {table_id}")


if __name__ == "__main__":
    main()
