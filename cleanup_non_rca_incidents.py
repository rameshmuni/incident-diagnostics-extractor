"""
Finds and optionally deletes any incident on this instance that is NOT one of the
50 synthetic UiPath RCA records this project seeds (uipath_rca_dataset.RCA_RECORDS).
Matches by short_description against the known set - anything else (ServiceNow's
default demo/sample data, stray test incidents created while debugging auth, etc.)
gets flagged. Defaults to a dry run: nothing is deleted until you explicitly ask for it.
"""
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

from uipath_rca_dataset import RCA_RECORDS

load_dotenv(Path(__file__).resolve().parent / ".env")

INSTANCE = os.environ["SN_INSTANCE"]
USER = os.environ["SN_USER"]
PASSWORD = os.environ["SN_PASS"]
CLIENT_ID = os.environ["SN_CLIENT_ID"]
CLIENT_SECRET = os.environ["SN_CLIENT_SECRET"]

BASE_URL = f"https://{INSTANCE}.service-now.com/api/now"
TOKEN_URL = f"https://{INSTANCE}.service-now.com/oauth_token.do"

KNOWN_SHORT_DESCRIPTIONS = {r["short_description"] for r in RCA_RECORDS}


def _fetch_oauth_token():
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
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Authorization": f"Bearer {_ACCESS_TOKEN}",
}


def list_all_incidents(limit=1000):
    # no state filter - catches leftover demo/sample data in ANY state, not just resolved
    resp = requests.get(
        f"{BASE_URL}/table/incident",
        headers=HEADERS, timeout=30,
        params={
            "sysparm_fields": "sys_id,number,short_description,state",
            "sysparm_display_value": "true",  # human-readable state label instead of a raw code
            "sysparm_limit": limit,
        },
    )
    if not resp.ok:
        raise RuntimeError(f"{resp.status_code} on {resp.url}\n{resp.text[:300]}")
    return resp.json()["result"]


def find_non_rca_incidents():
    incidents = list_all_incidents()
    ours, others = [], []
    for inc in incidents:
        (ours if inc["short_description"] in KNOWN_SHORT_DESCRIPTIONS else others).append(inc)
    return ours, others


def delete_incident(sys_id):
    resp = requests.delete(f"{BASE_URL}/table/incident/{sys_id}", headers=HEADERS, timeout=30)
    if resp.status_code not in (200, 204):
        raise RuntimeError(f"Delete failed {resp.status_code} on {sys_id}: {resp.text[:300]}")


def main(dry_run=True):
    ours, others = find_non_rca_incidents()
    print(f"Matched {len(ours)} incident(s) to the 50-record RCA seed - keeping these.")
    print(f"Found {len(others)} incident(s) NOT in the RCA set:\n")
    for inc in others:
        print(f"  {inc['number']}  [{inc['state']}]  {inc['short_description']}")

    if dry_run:
        print("\nDry run only - nothing deleted.")
        print("Re-run as: python3 -c \"import cleanup_non_rca_incidents as c; c.main(dry_run=False)\"")
        return others

    for inc in others:
        delete_incident(inc["sys_id"])
        print(f"Deleted {inc['number']}")
    print(f"\nDeleted {len(others)} incident(s).")
    return others


if __name__ == "__main__":
    main(dry_run=True)
