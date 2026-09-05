"""
One-off cleanup for incidents created by running seed_resolved_incidents.py more than once.

seed_resolved_incidents.py has no idempotency check - every run creates a brand new incident
for every record in RCA_RECORDS, resolved with the exact same short_description + close_notes
as any earlier run. Running it twice therefore doesn't update anything, it just adds a second
copy of all 50 records under new incident numbers - which is exactly what's been retrieved as
"different" matches (e.g. INC0010045 and INC0010100 sharing one short_description) even though
they're really the same seed record twice.

This script finds those duplicate groups and deletes all but one copy of each, WITHOUT
touching anything else in the instance (escalated tickets, incidents that just happen to look
similar, anything not created by the seeder). It defaults to a dry run - it only prints what it
would do and writes an audit log - and only deletes when you pass --execute.

Usage:
    python3 dedupe_incidents.py            # dry run: prints the plan, writes the audit log
    python3 dedupe_incidents.py --execute   # actually deletes the extra copies

After it deletes anything, re-run the refresh (locally: fetch_resolved_incidents.py then
build_bigquery_corpus.py; on Cloud Run: `gcloud run jobs execute incident-refresh --region
us-central1`) so the corpus reflects the cleaned-up incident list. No rebuild/redeploy needed -
this script doesn't change any code, just ServiceNow data.
"""

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

import requests

from seed_resolved_incidents import BASE_URL, AUTH, HEADERS, _check, _parse_json

FIELDS = "sys_id,number,short_description,category,priority,close_notes,sys_created_on"
AUDIT_LOG = "dedupe_audit.jsonl"


def fetch_all_resolved(limit=1000):
    # same state=6 (Resolved) filter fetch_resolved_incidents.py uses - duplicates only ever
    # come from the seeder, which only ever creates+resolves incidents, so this is the whole
    # population any duplicate could be sitting in
    resp = _check(requests.get(
        f"{BASE_URL}/table/incident", auth=AUTH, headers=HEADERS, timeout=60,
        params={
            "sysparm_query": "state=6^ORDERBYsys_created_on",  # ascending: oldest copy first per group
            "sysparm_limit": limit,
            "sysparm_fields": FIELDS,
        },
    ))
    return _parse_json(resp)["result"]


def group_duplicates(incidents):
    # exact-match on both fields, not just short_description - two records only count as the
    # same seed record if their resolution write-up matches too, so an unrelated incident that
    # coincidentally shares a title (like INC0010065 vs the 2FA pair) is never touched
    groups = defaultdict(list)
    for inc in incidents:
        key = ((inc.get("short_description") or "").strip(), (inc.get("close_notes") or "").strip())
        groups[key].append(inc)
    return {k: v for k, v in groups.items() if len(v) > 1}


def delete_incident(sys_id):
    resp = _check(requests.delete(f"{BASE_URL}/table/incident/{sys_id}", auth=AUTH, headers=HEADERS, timeout=30))
    return resp.status_code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="actually delete the extra copies (default: dry run)")
    parser.add_argument("--limit", type=int, default=1000, help="max resolved incidents to fetch (default 1000)")
    args = parser.parse_args()

    incidents = fetch_all_resolved(limit=args.limit)
    print(f"Fetched {len(incidents)} resolved incident(s).")

    dup_groups = group_duplicates(incidents)
    if not dup_groups:
        print("No duplicates found - nothing to do.")
        return

    to_keep, to_delete = [], []
    for (short_desc, _close_notes), members in dup_groups.items():
        # already sorted ascending by sys_created_on from the query, so the first member is the oldest
        keep, extras = members[0], members[1:]
        to_keep.append(keep)
        to_delete.extend(extras)
        print(f"\nDuplicate group ({len(members)} copies): {short_desc[:70]!r}")
        print(f"  keep:   {keep['number']}  (created {keep['sys_created_on']})")
        for extra in extras:
            print(f"  delete: {extra['number']}  (created {extra['sys_created_on']})")

    print(f"\n{len(dup_groups)} duplicate group(s), {len(to_delete)} extra incident(s) to delete, "
          f"{len(to_keep)} canonical copy/copies kept.")

    audit_entries = [
        {
            "number": inc["number"],
            "sys_id": inc["sys_id"],
            "short_description": inc["short_description"],
            "sys_created_on": inc["sys_created_on"],
        }
        for inc in to_delete
    ]
    Path(AUDIT_LOG).write_text("\n".join(json.dumps(e) for e in audit_entries) + "\n")
    print(f"Wrote {len(audit_entries)} planned deletion(s) to {AUDIT_LOG} (keep this in case you need the numbers later).")

    if not args.execute:
        print("\nDry run only - nothing was deleted. Re-run with --execute to actually delete these.")
        return

    print("\nDeleting...")
    for i, inc in enumerate(to_delete, start=1):
        delete_incident(inc["sys_id"])
        print(f"[{i}/{len(to_delete)}] Deleted {inc['number']}")
        time.sleep(0.3)  # be polite to the PDI rate limits, same pause seed() uses

    print(f"\nDeleted {len(to_delete)} duplicate incident(s). Now re-run the refresh so the corpus "
          f"picks up the cleaned-up list:\n"
          f"  locally:   python3 fetch_resolved_incidents.py && python3 build_bigquery_corpus.py\n"
          f"  Cloud Run: gcloud run jobs execute incident-refresh --region us-central1")


if __name__ == "__main__":
    main()
