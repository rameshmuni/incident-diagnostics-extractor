"""
Wraps week3/auto_remediation.py's match-then-fix logic. Nothing about
similarity matching, the confidence threshold, or the known-issue catalog is
reimplemented here - this only gives it a name that doesn't reference
"week3" or "auto_remediation" directly, so a consumer of the SDK isn't
coupled to this project's own folder layout.

Two remediation flows live here, deliberately kept separate:

  attempt()            - the fully automatic path, unchanged: match, run the
                          fix, resolve - no human involved. This is exactly
                          what's deployed today (attempt_auto_remediation()
                          in week3/auto_remediation.py). Kept as-is, for
                          comparison and for anything already relying on it.

  propose() + run_confirmed_fix() - the flow to actually use going forward,
                          per direction that a human must approve before ANY
                          remedy script runs, no exceptions, regardless of
                          match confidence. propose() only matches - it never
                          runs a fix or writes to ServiceNow. run_confirmed_fix()
                          is the only method in this SDK that actually executes
                          a remedy, and it must be called with an issue_id a
                          human has already approved.
"""


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

    def _entry_for(self, issue_id: str):
        entry = next((e for e in self._ar.CATALOG if e["issue_id"] == issue_id), None)
        if entry is None:
            known = [e["issue_id"] for e in self._ar.CATALOG]
            raise ValueError(f"unknown issue_id {issue_id!r} - expected one of {known}")
        return entry
