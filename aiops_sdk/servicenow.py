"""
Wraps this project's existing ServiceNow REST calls (seed_resolved_incidents.py
for create/resolve, query_incident.py for the escalation path) behind one
object with a handful of verbs. No new HTTP logic, auth handling, or
error-parsing lives in this file for create/resolve/escalate -
_check()/_parse_json()/AUTH/HEADERS all stay exactly where they already are.
list_open() below is the one genuinely new HTTP call this class adds - a
plain GET against the same Incident table API create()/resolve() already
use, needed so the AIOps console app can show a live inbox instead of a
copy of state kept in this process.
"""


class ServiceNowClient:
    """create / resolve / escalate / list_open - everything this project
    ever does to a ServiceNow incident, wrapped as one object instead of
    separate module-level imports."""

    def __init__(self):
        # imported inside __init__, not at module load time - constructing
        # this class is the moment SN_INSTANCE / SN_USER / SN_PASS actually
        # need to be set, same as importing seed_resolved_incidents.py
        # directly already requires today.
        import seed_resolved_incidents as _sn
        import query_incident as _qi

        self._sn = _sn
        self._qi = _qi

    def create(self, record: dict, caller_sys_id: str = None):
        """Open a new incident. record needs short_description, description,
        category, priority - see seed_resolved_incidents.create_incident()."""
        caller_sys_id = caller_sys_id or self._sn.get_caller_sys_id()
        return self._sn.create_incident(record, caller_sys_id)

    def resolve(self, sys_id: str, record: dict):
        """Close out an incident with a Root Cause + Resolution write-up.
        record needs root_cause and resolution keys."""
        return self._sn.resolve_incident(sys_id, record)

    def escalate(self, incident_text: str, matches=None, log_file=None):
        """Open a fresh, unresolved ticket for a developer - the same path
        Week 2's "none of these apply" and Week 3's low-confidence outcome
        both already use. Nothing here is auto-resolved."""
        kwargs = {}
        if log_file is not None:
            kwargs["log_file"] = log_file
        return self._qi.escalate_to_developer(incident_text, matches or [], **kwargs)

    @property
    def escalation_log(self) -> str:
        """The filename query_incident.py's escalate_to_developer() logs
        escalated incidents to - handed back so callers don't need to
        import query_incident.ESCALATION_LOG directly."""
        return self._qi.ESCALATION_LOG

    def list_open(self, description_contains: str = None, limit: int = 50):
        """Every incident ServiceNow considers still active (not resolved
        or closed), optionally narrowed to ones whose description contains
        a given substring - a plain GET against the same table/incident
        endpoint create()/resolve() already POST/PATCH to. This is what lets
        the AIOps console app show a live inbox: it asks ServiceNow
        directly, on every page load, rather than trusting any state cached
        in a Python process.

        Filters on the standard `active` boolean rather than a specific
        numeric `state` value - state-code-to-label mappings ("1" = New,
        etc.) can be customized per ServiceNow instance, but `active` means
        the same thing ("not yet resolved/closed") everywhere... in theory.
        In practice, stock ServiceNow only flips `active` to false when an
        incident reaches Closed (state 7) - a merely Resolved (state 6)
        incident, which is all resolve_incident()/resolve() ever sets, can
        stay `active=true` for days until an auto-close business rule (if
        this instance even has one configured) eventually closes it. So
        `active=true` alone let already-resolved candidates keep reappearing
        here. `stateNOT IN6,7` is added alongside it as a second, explicit
        filter that doesn't depend on `active` being wired up the way stock
        ServiceNow assumes - either filter excluding a record is enough.

        Likewise, `description_contains` uses ServiceNow's LIKE operator
        against the free-text `description` field rather than an exact-match
        `category`, because `category` is normally a fixed dropdown and a
        value outside that list can be silently rejected or blanked by the
        instance with no error raised - description has no such restriction.
        """
        import requests

        query_parts = ["active=true", "stateNOT IN6,7"]
        if description_contains:
            query_parts.append(f"descriptionLIKE{description_contains}")

        resp = requests.get(
            f"{self._sn.BASE_URL}/table/incident",
            auth=self._sn.AUTH,
            headers=self._sn.HEADERS,
            timeout=30,
            params={
                "sysparm_query": "^".join(query_parts),
                "sysparm_fields": "sys_id,number,short_description,description,priority,category,state,opened_at",
                "sysparm_limit": limit,
            },
        )
        self._sn._check(resp)
        return self._sn._parse_json(resp)["result"]
