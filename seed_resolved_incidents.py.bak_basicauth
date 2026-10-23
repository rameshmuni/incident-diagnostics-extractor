import os
import time
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

from uipath_rca_dataset import RCA_RECORDS

load_dotenv(Path(__file__).resolve().parent / ".env")

INSTANCE = os.environ["SN_INSTANCE"]
USER = os.environ["SN_USER"]
PASSWORD = os.environ["SN_PASS"]

BASE_URL = f"https://{INSTANCE}.service-now.com/api/now"
AUTH = HTTPBasicAuth(USER, PASSWORD)
HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}


def _check(resp):
    # surface ServiceNow's own error detail instead of a bare HTTPError -
    # the error.detail field almost always names the exact ACL/business rule that fired
    if not resp.ok:
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text[:300]
        raise RuntimeError(f"{resp.status_code} on {resp.url}\n{detail}")
    return resp


def _parse_json(resp):
    # ServiceNow can return 200 with an empty/HTML body when the PDI is hibernating -
    # surface that clearly instead of letting the raw JSONDecodeError confuse things
    try:
        return resp.json()
    except ValueError:
        raise RuntimeError(
            f"Non-JSON response from {resp.url} (status {resp.status_code}). "
            f"If this is empty or HTML, your PDI is probably hibernating - "
            f"log into developer.servicenow.com and wake the instance, then retry.\n"
            f"Raw body (first 300 chars): {resp.text[:300]!r}"
        )


def get_caller_sys_id():
    # resolving an incident on this instance requires a Caller - reuse the admin account for that
    resp = _check(requests.get(
        f"{BASE_URL}/table/sys_user", auth=AUTH, headers=HEADERS, timeout=30,
        params={"sysparm_query": f"user_name={USER}", "sysparm_fields": "sys_id", "sysparm_limit": 1},
    ))
    return _parse_json(resp)["result"][0]["sys_id"]


def create_incident(record, caller_sys_id):
    # open the incident with the symptom side of the story plus a caller (mandatory to resolve later)
    payload = {
        "short_description": record["short_description"],
        "description": record["description"],
        "category": record["category"],
        "priority": record["priority"],
        "caller_id": caller_sys_id,
    }
    resp = _check(requests.post(f"{BASE_URL}/table/incident", auth=AUTH, headers=HEADERS, timeout=30, json=payload))
    return _parse_json(resp)["result"]


def resolve_incident(sys_id, record):
    # close it out with the full RCA text as close_notes - this is the corpus text Week 2 embeds
    close_notes = (
        f"Root Cause:\n{record['root_cause']}\n\n"
        f"Resolution:\n{record['resolution']}"
    )
    payload = {
        "state": "6",
        "close_code": "Solution provided",  # confirmed valid choice value on this instance
        "close_notes": close_notes,
    }
    resp = _check(requests.patch(f"{BASE_URL}/table/incident/{sys_id}", auth=AUTH, headers=HEADERS, timeout=30, json=payload))
    return _parse_json(resp)["result"]


def seed(records=RCA_RECORDS, pause=0.3):
    # entry point: create and immediately resolve every record, so the corpus is ready for Week 2
    caller_sys_id = get_caller_sys_id()
    created = []
    for i, record in enumerate(records, start=1):
        incident = create_incident(record, caller_sys_id)
        resolve_incident(incident["sys_id"], record)
        print(f"[{i}/{len(records)}] {incident['number']}  {record['short_description'][:60]}")
        created.append(incident["number"])
        time.sleep(pause)  # be polite to the PDI rate limits

    print(f"\nSeeded and resolved {len(created)} incident(s).")
    return created


if __name__ == "__main__":
    seed()
