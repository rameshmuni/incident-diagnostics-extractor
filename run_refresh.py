"""
Batch entrypoint for the Cloud Run JOB (not the web service).

This replaces "SSH into your laptop and run two commands by hand" with one command
Cloud Run can run to completion on a schedule or on demand: pull whatever's newly
resolved in ServiceNow, then rebuild the corpus the assistant searches against.

Locally this was always two separate commands:
    python3 fetch_resolved_incidents.py
    python3 build_bigquery_corpus.py
This script just calls the same two entry points back to back, so Cloud Run Jobs
has exactly one command to run and exit - Jobs are built for "run to completion and
stop," not for chaining shell commands.

Note this Job needs GEMINI_API_KEY now (build_bigquery_corpus.py calls Gemini to embed
every record), on top of the SN_INSTANCE/SN_USER/SN_PASS it already needed - see the
deployment guide's secrets list for this Job.
"""

import sys

import build_bigquery_corpus
import fetch_resolved_incidents


def main():
    print("=== Step 1/2: fetching resolved incidents from ServiceNow ===")
    records = fetch_resolved_incidents.main()
    print(f"\n=== Step 2/2: embedding and loading {len(records)} record(s) into BigQuery ===")
    build_bigquery_corpus.main()
    print("\nRefresh complete.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - a non-zero exit is how a Cloud Run Job reports failure
        print(f"Refresh failed: {exc}", file=sys.stderr)
        sys.exit(1)
