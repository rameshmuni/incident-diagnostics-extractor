"""
Week 3: given a new incident's text, decide whether it's one of a small, known set of
auto-fixable issues - and if so, actually run the fix and resolve the incident, with no
human confirmation step. This is what api_query() in app.py calls when the human-in-the-loop
toggle is switched off; when it's on, app.py never touches this file at all and Week 2's
retrieve-then-ask-a-human flow runs exactly as before.

Deliberately scoped small, per the actual Week 3 ask: this does NOT try to auto-remediate
anything and everything. It only acts when the incoming incident confidently matches one of
a handful of known issues that already have a real remedy script behind them (remedies.py).
Anything else - including anything merely *similar* to a known issue but not a confident
match - gets escalated to a new ServiceNow ticket for a developer, exactly like Week 2's
"none of these apply" path. Auto-remediation is meant to be trustworthy on a small, well-
understood set of problems, not a guess-and-hope net over arbitrary incidents.
"""

import math

# these two are Week 2's shared core, one level up in scripts/ (not part of this package
# on purpose - query_incident.py and seed_resolved_incidents.py are used by BOTH weeks, so
# they stay put rather than getting duplicated or pulled in here). scripts/ is on sys.path
# because that's the app's working directory - see app.py.
from query_incident import embed_text, escalate_to_developer
from seed_resolved_incidents import BASE_URL, AUTH, HEADERS, _check, _parse_json, get_caller_sys_id
import requests
from . import remedies

# Cosine similarity threshold an incident's embedding must clear against a catalog entry
# before its remedy is allowed to run automatically. This is the actual safety valve on
# "the bot fixes things by itself" - tune it up if it ever fires on something it shouldn't,
# tune it down if genuinely matching incidents aren't clearing it. Started conservative.
AUTO_FIX_CONFIDENCE_THRESHOLD = 0.55

# The known, small set of issues this system is allowed to fix by itself. Each entry's
# short_description is the "question side" text the incoming incident gets compared against -
# written the way a person would actually describe the symptom, not the fix. These 4 map
# 1:1 onto the demo app's 4 scenarios (mock_systems.py / demo_app.py / remedies.py).
CATALOG = [
    {
        "issue_id": "app_down",
        "short_description": (
            "The application is completely down or unreachable for all users - the page "
            "fails to load entirely, times out, or returns a server unavailable error."
        ),
        "remedy": remedies.fix_app_down,
    },
    {
        "issue_id": "login_ui_disabled",
        "short_description": (
            "The Sign In / login button on the login page is missing, greyed out, or "
            "disabled, so no one can attempt to log in at all."
        ),
        "remedy": remedies.fix_login_ui_disabled,
    },
    {
        "issue_id": "login_cred_backend",
        "short_description": (
            "Users enter correct login credentials but sign-in still fails - the application "
            "appears unable to fetch or verify credentials against its backend, not a "
            "wrong-password problem."
        ),
        "remedy": remedies.fix_login_cred_backend,
    },
    {
        "issue_id": "upload_permission",
        "short_description": (
            "A user tries to upload a file and the upload fails with a permission error - "
            "the application cannot write the uploaded file to its storage folder."
        ),
        "remedy": remedies.fix_upload_permission,
    },
]

_catalog_embeddings_cache = None


def _catalog_embeddings():
    # computed once per process and cached, same pattern as the genai/bigquery client
    # caches in query_incident.py - only 4 embed calls, ever, per cold start
    global _catalog_embeddings_cache
    if _catalog_embeddings_cache is None:
        _catalog_embeddings_cache = [
            (entry, embed_text(entry["short_description"], task_type="RETRIEVAL_DOCUMENT"))
            for entry in CATALOG
        ]
    return _catalog_embeddings_cache


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def match_auto_fix(incident_text):
    """
    Returns (catalog_entry, similarity) for the best-matching known issue if it clears the
    confidence threshold, else (None, best_similarity_seen) - the caller can still log/show
    how close the nearest known issue was, even when it wasn't close enough to act on.

    This runs entirely in Python against 4 cached vectors rather than a BigQuery
    VECTOR_SEARCH query, on purpose - at only 4 catalog entries, a whole extra table and
    refresh pipeline (like incident_corpus has) would be more machinery than the problem
    needs. Same embedding model and same cosine-similarity reasoning as Week 2 either way.
    """
    query_vector = embed_text(incident_text, task_type="RETRIEVAL_QUERY")
    best_entry, best_similarity = None, -1.0
    for entry, vector in _catalog_embeddings():
        similarity = _cosine(query_vector, vector)
        if similarity > best_similarity:
            best_entry, best_similarity = entry, similarity
    if best_similarity >= AUTO_FIX_CONFIDENCE_THRESHOLD:
        return best_entry, best_similarity
    return None, best_similarity


def _create_incident(new_incident_text):
    # opens the ticket, unresolved (state defaults to New) - split out from the old
    # _create_and_resolve_incident() so the event-driven path below (raise_event_incident())
    # can create it separately from when it gets fixed, instead of both happening in the
    # same instant. attempt_auto_remediation() still calls this immediately followed by
    # _resolve_incident() below, so its own behavior is unchanged.
    caller_sys_id = get_caller_sys_id()
    payload = {
        "short_description": new_incident_text[:160],
        "description": (
            "Auto-detected as a known, auto-fixable issue by the RAG assistant "
            "(human-in-the-loop off). No human reviewed this before it was resolved.\n\n"
            f"{new_incident_text}"
        ),
        "category": "Software",
        "priority": "3",
        "caller_id": caller_sys_id,
    }
    resp = _check(requests.post(f"{BASE_URL}/table/incident", auth=AUTH, headers=HEADERS, timeout=30, json=payload))
    return _parse_json(resp)["result"]


def _resolve_incident(sys_id, resolution_text):
    # mirrors resolve_incident() in seed_resolved_incidents.py - closes an already-open
    # incident (by sys_id) with the remedy's own Root Cause + Resolution text. This is the
    # actual "update the same resolution in the incident" / "automatically update
    # ServiceNow" step the manager and the Week 3 assignment both asked for.
    resolve_payload = {
        "state": "6",
        "close_code": "Solution provided",
        "close_notes": resolution_text,
    }
    resp = _check(requests.patch(
        f"{BASE_URL}/table/incident/{sys_id}", auth=AUTH, headers=HEADERS, timeout=30, json=resolve_payload
    ))
    return _parse_json(resp)["result"]


def attempt_auto_remediation(new_incident_text):
    """
    The one function app.py calls when human-in-the-loop is off (a human typed a symptom
    into the chat and switched HIL off). Always returns a dict describing exactly what
    happened - there's no "pending" state coming out of this: by the time this returns, the
    incident has already been either resolved or escalated in ServiceNow.
    """
    entry, similarity = match_auto_fix(new_incident_text)

    if entry is None:
        # not a confident match for anything this system knows how to fix by itself -
        # reuse Week 2's exact escalation path rather than inventing a second one.
        # matches=[] because no corpus retrieval happened on this path at all.
        incident = escalate_to_developer(new_incident_text, matches=[])
        return {
            "outcome": "escalated",
            "best_similarity": round(similarity, 3),
            "incident_number": incident["number"] if incident else None,
        }

    resolution_text = entry["remedy"](new_incident_text)
    incident = _create_incident(new_incident_text)
    incident = _resolve_incident(incident["sys_id"], resolution_text)
    return {
        "outcome": "auto_resolved",
        "issue_id": entry["issue_id"],
        "similarity": round(similarity, 3),
        "resolution_text": resolution_text,
        "incident_number": incident["number"],
    }


def _catalog_entry(issue_id):
    entry = next((e for e in CATALOG if e["issue_id"] == issue_id), None)
    if entry is None:
        raise ValueError(f"unknown issue_id {issue_id!r} - expected one of {[e['issue_id'] for e in CATALOG]}")
    return entry


def raise_event_incident(issue_id):
    """
    The actual "webhook simulating a system alert" from the Week 3 assignment. Called by
    app.py's /api/demo/break right after a known scenario is broken - this is the moment a
    real monitoring system would have noticed the fault and opened a ticket, so this opens a
    REAL ServiceNow incident describing the symptom (state: New), genuinely alerted rather
    than typed by a human. Deliberately does NOT run the remedy yet - the demo app stays
    visibly broken (and the incident stays open) until resolve_event_incident() below is
    called separately, the same way a real alert doesn't fix itself the instant it fires.

    No embedding/matching step is needed here (unlike attempt_auto_remediation) because the
    caller already knows exactly which known issue this is - it's the one that was just
    broken - so this always uses the catalog's own short_description as the incident text.
    """
    entry = _catalog_entry(issue_id)
    incident = _create_incident(entry["short_description"])
    return {"incident_number": incident["number"], "incident_sys_id": incident["sys_id"]}


def resolve_event_incident(issue_id, sys_id=None):
    """
    The other half of the event-driven loop: runs the known remedy for issue_id (fixing the
    demo app for real) and resolves the exact incident raise_event_incident() opened for it -
    same incident, not a new one - with the remedy's Root Cause + Resolution write-up. This
    is what app.py's /api/demo/fix calls.

    sys_id is optional: if this scenario was broken some other way than through
    /api/demo/break (e.g. locally via demo_toggle.py, which never talks to ServiceNow at
    all), there's no tracked incident to resolve - the remedy still runs and fixes the app
    for real, it just has nothing to PATCH in ServiceNow.
    """
    entry = _catalog_entry(issue_id)
    resolution_text = entry["remedy"](entry["short_description"])
    incident_number = None
    if sys_id:
        incident = _resolve_incident(sys_id, resolution_text)
        incident_number = incident["number"]
    return {"resolution_text": resolution_text, "incident_number": incident_number}
