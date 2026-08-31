import json
import os
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

INSTANCE = os.environ["SN_INSTANCE"]
USER = os.environ["SN_USER"]
PASSWORD = os.environ["SN_PASS"]

BASE_URL = f"https://{INSTANCE}.service-now.com/api/now"
AUTH = HTTPBasicAuth(USER, PASSWORD)
HEADERS = {"Accept": "application/json"}


def fetch_resolved_incidents(limit=200):
    # pull every resolved incident along with the RCA text written into close_notes
    query = "state=6^ORDERBYDESCsys_updated_on"
    fields = "sys_id,number,short_description,category,priority,close_notes"
    resp = requests.get(
        f"{BASE_URL}/table/incident",
        auth=AUTH, headers=HEADERS, timeout=30,
        params={"sysparm_query": query, "sysparm_limit": limit, "sysparm_fields": fields},
    )
    resp.raise_for_status()
    return resp.json()["result"]


def to_corpus_records(incidents):
    # flatten each incident down to just what the embedding step needs: an id and one text blob
    records = []
    for inc in incidents:
        if not inc.get("close_notes"):
            continue  # nothing to embed without a resolution write-up
        text = f"{inc['short_description']}\n\n{inc['close_notes']}"
        records.append({
            "incident_id": inc["number"],
            "sys_id": inc["sys_id"],
            "short_description": inc["short_description"],
            "category": inc.get("category"),
            "text": text,
        })
    return records


def main(out_file="resolved_incidents_corpus.json"):
    # entry point: pull everything resolved and save it as the raw corpus for the index-building step
    incidents = fetch_resolved_incidents()
    records = to_corpus_records(incidents)
    Path(out_file).write_text(json.dumps(records, indent=2))
    print(f"Pulled {len(incidents)} resolved incident(s), wrote {len(records)} corpus record(s) to {out_file}")
    return records


if __name__ == "__main__":
    main()
