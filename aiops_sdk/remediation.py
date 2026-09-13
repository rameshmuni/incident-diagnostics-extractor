"""
Wraps week3/auto_remediation.py's match-then-fix logic. Nothing about
similarity matching, the confidence threshold, or the known-issue catalog is
reimplemented here - this only gives it a name that doesn't reference
"week3" or "auto_remediation" directly, so a consumer of the SDK isn't
coupled to this project's own folder layout.

Three remediation flows live here now, each answering a different question
about who is allowed to run a remedy script:

  attempt()            - the fully automatic path, unchanged: match, run the
                          fix, resolve - no human involved anywhere. This is
                          exactly attempt_auto_remediation() in
                          week3/auto_remediation.py. Kept as-is, for
                          comparison with what's deployed today - nothing
                          currently calls this.

  propose() + run_confirmed_fix() - a human approves before a fix runs, but
                          the person proposing and the person approving can
                          be the same person in the same browser tab, in the
                          same request/response cycle. Useful for a single
                          combined app; doesn't answer "should the person who
                          merely reported the symptom be the one who's
                          allowed to approve it" - see below for the flow
                          that does.

  raise_remedy_candidate() + list_remedy_candidates() +
  run_confirmed_fix_for_candidate() - the split-audience flow: an end user
                          (or their app) only ever calls
                          raise_remedy_candidate(), which never runs
                          anything - it opens a real, unresolved ServiceNow
                          incident tagged with CANDIDATE_TAG and returns.
                          Nothing else happens until someone with access to
                          a *different* app (aiops_console_app) calls
                          list_remedy_candidates() to see it, reviews
                          REMEDY_EXPLANATIONS for what the script would
                          actually do, and calls
                          run_confirmed_fix_for_candidate() to run it and
                          resolve that same incident. The ServiceNow incident
                          itself is the hand-off between the two apps - there
                          is no shared Python process, no shared file, just
                          the same ticket both apps can see.
"""

import re

# The one thing distinguishing a "waiting for AIOps approval" incident from
# every other incident this project creates. This used to be a custom
# `category` value - but `category` on the Incident table is normally a
# fixed dropdown (Software/Hardware/Network/...), and a value outside that
# list can get silently rejected or blanked by ServiceNow depending on the
# instance's own configuration, with no error raised anywhere in this
# project's code - the incident still gets created, just without the tag
# that was supposed to be on it. A literal marker at the start of
# `description` doesn't have that problem: description is free text on every
# ServiceNow instance, already proven to accept exactly what's written into
# it (every incident this project creates writes arbitrary text there).
# list_remedy_candidates() below filters on this string with ServiceNow's
# LIKE operator instead of an exact-match category.
CANDIDATE_TAG = "[AIOps Auto-Remediation Candidate]"

# Plain-language, pre-execution descriptions of what each remedy script
# actually does - for a person deciding whether to click "Approve & run" in
# aiops_console_app, before it runs. Deliberately separate from the Root
# Cause / Resolution text remedies.py returns AFTER running - that text
# describes what already happened; this describes what's about to.
REMEDY_EXPLANATIONS = {
    "app_down": (
        "Clears the internal 'app is down' flag so the demo app starts "
        "serving requests again. A toggle only - no files or configuration "
        "are touched."
    ),
    "login_ui_disabled": (
        "Re-enables the Sign In button on the login page. A toggle only - "
        "no files or configuration are touched."
    ),
    "login_cred_backend": (
        "Restores the login page's credential-verification backend config "
        "value to its working URL. A single config value only - no other "
        "settings or data are touched."
    ),
    "upload_permission": (
        "Runs chmod 755 on the application's uploads/ folder to restore "
        "write access. Only the folder's permission bits change - no files "
        "inside it are read, modified, or deleted."
    ),
}

# Matches the description raise_remedy_candidate() writes into a candidate
# incident, so list_remedy_candidates() can pull issue_id/similarity/the
# original report back out of it. A real production system would more
# likely use a dedicated ServiceNow field for this instead of parsing free
# text out of description - this is the pragmatic version that needs no
# ServiceNow schema changes to demo.
_CANDIDATE_DESCRIPTION_RE = re.compile(
    r"Matched issue:\s*(?P<issue_id>\S+)\s*\(similarity\s*(?P<similarity>[\d.]+)\).*?"
    r"Original report:\n(?P<incident_text>.*)",
    re.S,
)


class Remediator:
    """Matches a new incident against the known auto-fixable issue catalog.
    See week3/auto_remediation.py for the actual matching math and
    week3/remedies.py for what each fix actually does."""

    def __init__(self):
        # imported inside __init__, not at module load time - see the same
        # reasoning in retrieval.py and servicenow.py.
        from week3 import auto_remediation as _ar

        self._ar = _ar

    @property
    def catalog(self):
        """The known auto-fixable issues and their short descriptions."""
        return self._ar.CATALOG

    @property
    def confidence_threshold(self) -> float:
        return self._ar.AUTO_FIX_CONFIDENCE_THRESHOLD

    def match(self, incident_text: str):
        """(catalog_entry, similarity) - catalog_entry is None if nothing
        cleared the confidence threshold. Read-only - matching alone never
        runs a fix or touches ServiceNow, whichever flow calls it."""
        return self._ar.match_auto_fix(incident_text)

    # ------------------------------------------------------------------
    # Fully automatic - unchanged, kept for comparison with what's deployed.
    # ------------------------------------------------------------------

    def attempt(self, incident_text: str) -> dict:
        """The original HIL-off flow in one call: match, run the real fix,
        create + resolve the ServiceNow incident - or escalate if nothing
        matched confidently. No human is involved anywhere in this method.
        This is exactly attempt_auto_remediation() in week3/auto_remediation.py,
        unmodified - see that function's own docstring."""
        return self._ar.attempt_auto_remediation(incident_text)

    # ------------------------------------------------------------------
    # Mandatory human-in-the-loop - the recommended flow going forward.
    # ------------------------------------------------------------------

    def propose(self, incident_text: str) -> dict:
        """Step 1 of the mandatory-HIL flow, and the ONLY step that runs
        without a human. Matches the incident against the known-issue
        catalog and returns what a fix WOULD do - it never calls a remedy
        function and never creates or touches a ServiceNow incident.

        Always the first call for every incident, no exceptions - including
        ones that matched with very high confidence. A caller shows this
        result to a person; run_confirmed_fix() is the only next step that
        actually does anything.
        """
        entry, similarity = self._ar.match_auto_fix(incident_text)
        if entry is None:
            return {
                "decision": "no_match",
                "best_similarity": round(similarity, 3),
                "message": "No known issue matched confidently enough to propose a fix for.",
            }
        return {
            "decision": "needs_human_approval",
            "issue_id": entry["issue_id"],
            "matched_description": entry["short_description"],
            "similarity": round(similarity, 3),
        }

    def run_confirmed_fix(self, incident_text: str, issue_id: str, approved_by: str = None) -> dict:
        """Step 2 - the only method in this SDK that actually runs a remedy
        script. Must only be called after a human has seen propose()'s
        result for this same incident_text and explicitly approved
        issue_id. There is no similarity check in here and no way to skip
        the approval step through this method - the caller deciding to call
        it at all IS the approval gate.

        approved_by is optional free text (a name, a username) recorded in
        the ServiceNow incident's description, purely for the audit trail -
        it changes nothing about whether the fix runs.
        """
        entry = self._entry_for(issue_id)
        resolution_text = entry["remedy"](incident_text)

        # local import, not at module load time - same reasoning as __init__
        from .servicenow import ServiceNowClient

        sn = ServiceNowClient()
        approval_note = f" (approved by {approved_by})" if approved_by else ""
        incident = sn.create({
            "short_description": incident_text[:160],
            "description": (
                "Matched to a known auto-fixable issue by the AIOps assistant "
                f"and approved by a human before the fix ran{approval_note}.\n\n"
                f"{incident_text}"
            ),
            "category": "Software",
            "priority": "3",
        })
        incident = sn.resolve(incident["sys_id"], {
            "root_cause": entry["short_description"],
            "resolution": resolution_text,
        })
        return {
            "outcome": "resolved_with_approval",
            "issue_id": entry["issue_id"],
            "resolution_text": resolution_text,
            "incident_number": incident["number"],
            "approved_by": approved_by,
        }

    # ------------------------------------------------------------------
    # Split-audience flow - an end user's app only ever calls the first
    # method below; only aiops_console_app ever calls the last one.
    # ------------------------------------------------------------------

    def raise_remedy_candidate(self, incident_text: str) -> dict:
        """The only remediation-related call an end-user-facing app should
        ever make. Matches the incident against the known-issue catalog; if
        confident, opens a real, unresolved ServiceNow incident tagged with
        CANDIDATE_TAG and returns immediately - no remedy function is
        called, nothing is resolved, here or anywhere else in this method.
        If nothing matches confidently, escalates exactly like attempt() /
        propose() already do for that case, since there is nothing for
        AIOps to review in that case either."""
        entry, similarity = self._ar.match_auto_fix(incident_text)

        from .servicenow import ServiceNowClient
        sn = ServiceNowClient()

        if entry is None:
            incident = sn.escalate(incident_text)
            return {
                "decision": "escalated",
                "best_similarity": round(similarity, 3),
                "incident_number": incident["number"] if incident else None,
            }

        incident = sn.create({
            "short_description": incident_text[:160],
            "description": (
                f"{CANDIDATE_TAG}\n"
                f"Matched issue: {entry['issue_id']} (similarity {round(similarity, 3)})\n"
                "Waiting for an AIOps team member to review and approve in "
                "aiops_console_app before any remedy runs.\n\n"
                f"Original report:\n{incident_text}"
            ),
            # A real, valid category (matching what every other incident in
            # this project already uses) rather than a made-up one - see
            # CANDIDATE_TAG's comment for why this field isn't the tag.
            "category": "Software",
            "priority": "3",
        })
        return {
            "decision": "raised_for_aiops_review",
            "issue_id": entry["issue_id"],
            "matched_description": entry["short_description"],
            "similarity": round(similarity, 3),
            "incident_number": incident["number"],
            "sys_id": incident["sys_id"],
        }

    # ServiceNow incident state codes for Resolved / Closed. Checked again
    # here, in Python, even though list_open()'s own query already excludes
    # them - belt and suspenders after the bug where a resolved incident
    # (state 6) kept reappearing because `active` didn't flip to false on
    # this instance until Closed (state 7). If either the query filter or
    # this client-side check would exclude a record, it gets excluded.
    _RESOLVED_OR_CLOSED_STATES = {"6", "7"}

    def list_remedy_candidates(self) -> list:
        """aiops_console_app's entire inbox: every open incident
        raise_remedy_candidate() has opened and nobody has acted on yet,
        fetched live from ServiceNow (not from any state kept in this
        process), parsed back into issue_id/similarity/the original report,
        and paired with a plain-language explanation of what approving it
        would actually run."""
        from .servicenow import ServiceNowClient
        sn = ServiceNowClient()

        candidates = []
        for incident in sn.list_open(description_contains=CANDIDATE_TAG):
            if str(incident.get("state")) in self._RESOLVED_OR_CLOSED_STATES:
                continue  # already resolved/closed - don't show it again, whatever the query returned
            parsed = self._parse_candidate_description(incident.get("description", ""))
            if parsed is None:
                continue  # not one of ours, or written before this format existed
            issue_id, similarity, incident_text = parsed
            candidates.append({
                "number": incident["number"],
                "sys_id": incident["sys_id"],
                "issue_id": issue_id,
                "similarity": similarity,
                "incident_text": incident_text,
                "remedy_explanation": REMEDY_EXPLANATIONS.get(issue_id, "(no explanation available)"),
                "opened_at": incident.get("opened_at"),
            })
        return candidates

    def run_confirmed_fix_for_candidate(self, sys_id: str, incident_text: str, issue_id: str, approved_by: str = None) -> dict:
        """The only method in this SDK aiops_console_app calls to actually
        run something. Runs the real remedy and resolves the SAME incident
        raise_remedy_candidate() already opened (by sys_id) - it does not
        open a new one, since one already exists and is what the end user is
        watching for an update."""
        entry = self._entry_for(issue_id)
        resolution_text = entry["remedy"](incident_text)

        from .servicenow import ServiceNowClient
        sn = ServiceNowClient()
        approval_note = f" (approved by {approved_by})" if approved_by else ""
        incident = sn.resolve(sys_id, {
            "root_cause": entry["short_description"],
            "resolution": resolution_text + approval_note,
        })
        return {
            "outcome": "resolved_with_approval",
            "issue_id": entry["issue_id"],
            "resolution_text": resolution_text,
            "incident_number": incident["number"],
            "approved_by": approved_by,
        }

    def _parse_candidate_description(self, description: str):
        match = _CANDIDATE_DESCRIPTION_RE.search(description)
        if not match:
            return None
        return match.group("issue_id"), float(match.group("similarity")), match.group("incident_text").strip()

    def _entry_for(self, issue_id: str):
        entry = next((e for e in self._ar.CATALOG if e["issue_id"] == issue_id), None)
        if entry is None:
            known = [e["issue_id"] for e in self._ar.CATALOG]
            raise ValueError(f"unknown issue_id {issue_id!r} - expected one of {known}")
        return entry
