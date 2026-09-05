"""
Read-only diagnostic - does not delete or modify anything.

Prints a full breakdown of what's actually in the ServiceNow incident table right now, so we
can see exactly where a count like "120 total, ideally ~70" is coming from before deleting
anything else:
  1. How many incidents exist per state (state=6 is Resolved - that's the pool
     dedupe_incidents.py and fetch_resolved_incidents.py both operate on; anything in another
     state, e.g. escalated "New" tickets from the app's "None of these apply" flow, is
     invisible to both of those and was never touched by the last dedupe run).
  2. Within state=6 (Resolved) specifically: how many distinct short_description groups exist,
     how many are still duplicated (>1 incident sharing both short_description AND close_notes -
     the same exact-match definition dedupe_incidents.py used), and separately, groups that
     share a short_description but do NOT match exactly on close_notes (which would explain
     duplicates that look identical in the UI but silently survived the last dedupe pass).

Usage:
    python3 audit_incidents.py
"""

from collections import defaultdict

import requests

from seed_resolved_incidents import BASE_URL, AUTH, HEADERS, _check, _parse_json

FIELDS = "sys_id,number,state,short_description,category,close_notes,sys_created_on"


def fetch_all(limit=2000):
    # no state filter here on purpose - the whole point is to see everything, not just the
    # state=6 slice dedupe_incidents.py and the corpus-refresh pipeline already look at
    resp = _check(requests.get(
        f"{BASE_URL}/table/incident", auth=AUTH, headers=HEADERS, timeout=60,
        params={
            "sysparm_query": "ORDERBYsys_created_on",
            "sysparm_limit": limit,
            "sysparm_fields": FIELDS,
        },
    ))
    return _parse_json(resp)["result"]


def main():
    incidents = fetch_all()
    print(f"Total incidents in the instance: {len(incidents)}\n")

    by_state = defaultdict(list)
    for inc in incidents:
        by_state[inc.get("state")].append(inc)

    print("By state value:")
    for state, members in sorted(by_state.items(), key=lambda kv: -len(kv[1])):
        print(f"  state={state!r}: {len(members)}")
    print()

    resolved = by_state.get("6", [])
    print(f"Resolved (state=6): {len(resolved)} incident(s) - this is the pool the corpus is built from.\n")

    # exact-match groups: same definition dedupe_incidents.py used
    exact_groups = defaultdict(list)
    for inc in resolved:
        key = ((inc.get("short_description") or "").strip(), (inc.get("close_notes") or "").strip())
        exact_groups[key].append(inc)
    exact_dupes = {k: v for k, v in exact_groups.items() if len(v) > 1}

    # loose groups: same short_description only, regardless of close_notes content -
    # catches "looks identical in the UI" cases that wouldn't have matched the strict dedupe
    loose_groups = defaultdict(list)
    for inc in resolved:
        key = (inc.get("short_description") or "").strip()
        loose_groups[key].append(inc)
    loose_dupes = {k: v for k, v in loose_groups.items() if len(v) > 1}

    print(f"Distinct short_description values among resolved incidents: {len(loose_groups)}")
    print(f"  -> if every one of those is meant to be unique, that's your real 'ideal' corpus size.\n")

    print(f"Exact-match duplicate groups remaining (same short_description AND close_notes): {len(exact_dupes)}")
    for (short_desc, _notes), members in exact_dupes.items():
        nums = ", ".join(m["number"] for m in members)
        print(f"  {short_desc[:65]!r}: {len(members)} copies -> {nums}")

    print(f"\nSame-short_description-but-different-close_notes groups: {len(loose_dupes) - len(exact_dupes)}")
    for short_desc, members in loose_dupes.items():
        key_exact = None
        # skip ones already reported as exact matches above
        notes_set = {(m.get("close_notes") or "").strip() for m in members}
        if len(notes_set) > 1:
            nums = ", ".join(m["number"] for m in members)
            print(f"  {short_desc[:65]!r}: {len(members)} incidents, {len(notes_set)} distinct close_notes text(s) -> {nums}")
            for m in members:
                preview = (m.get("close_notes") or "")[:80].replace("\n", " ")
                print(f"      {m['number']} (created {m['sys_created_on']}): {preview!r}...")


if __name__ == "__main__":
    main()
