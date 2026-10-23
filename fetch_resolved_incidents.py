import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

INSTANCE = os.environ["SN_INSTANCE"]
USER = os.environ["SN_USER"]
PASSWORD = os.environ["SN_PASS"]
CLIENT_ID = os.environ["SN_CLIENT_ID"]
CLIENT_SECRET = os.environ["SN_CLIENT_SECRET"]

BASE_URL = f"https://{INSTANCE}.service-now.com/api/now"
TOKEN_URL = f"https://{INSTANCE}.service-now.com/oauth_token.do"


def _fetch_oauth_token():
    # Same reason as seed_resolved_incidents.py: this instance has Basic Auth disabled for
    # the REST API, so auth is the OAuth2 Resource Owner Password Credentials grant instead,
    # via the same Application Registry client used there.
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "username": USER,
            "password": PASSWORD,
        },
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"OAuth token request failed: {resp.status_code}\n{resp.text[:300]}")
    return resp.json()["access_token"]


_ACCESS_TOKEN = _fetch_oauth_token()
AUTH = None  # kept for parity with seed_resolved_incidents.py's pattern; unused below
HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {_ACCESS_TOKEN}",
}


def fetch_resolved_incidents(limit=200):
    # pull every resolved incident along with the RCA text written into close_notes
    query = "state=6^ORDERBYDESCsys_updated_on"
    fields = "sys_id,number,short_description,category,priority,close_notes"
    resp = requests.get(
        f"{BASE_URL}/table/incident",
        headers=HEADERS, timeout=30,
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
