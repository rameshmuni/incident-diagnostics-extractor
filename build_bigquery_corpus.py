import json
import time
from pathlib import Path

from google.cloud import bigquery

from query_incident import BQ_DATASET, embed_texts_batch, get_bq_client, table_ref

CORPUS_FILE = "resolved_incidents_corpus.json"
# chunk size for embedding calls - well under any documented/observed batch rate limit,
# and cuts a 100+ record corpus down to ~5 API calls instead of 100+ individual ones (the
# actual cause of the 429 RESOURCE_EXHAUSTED errors this replaced - see embed_texts_batch()
# and _embed_with_retry() in query_incident.py for the full story)
EMBED_BATCH_SIZE = 20
# small pause between chunks - extra headroom against per-minute quotas, on top of the
# retry/backoff embed_texts_batch() already does for an individual chunk
SECONDS_BETWEEN_BATCHES = 2

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
    #
    # Embedded in chunks (EMBED_BATCH_SIZE records per API call), not one record per call -
    # see embed_texts_batch() in query_incident.py for why that matters.
    for start in range(0, len(records), EMBED_BATCH_SIZE):
        chunk = records[start : start + EMBED_BATCH_SIZE]
        texts = [r["text"] for r in chunk]
        vectors = embed_texts_batch(texts, task_type="RETRIEVAL_DOCUMENT")
        for record, vector in zip(chunk, vectors):
            record["embedding"] = vector
        print(f"Embedded {start + len(chunk)}/{len(records)} record(s)...")
        if start + EMBED_BATCH_SIZE < len(records):
            time.sleep(SECONDS_BETWEEN_BATCHES)
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
