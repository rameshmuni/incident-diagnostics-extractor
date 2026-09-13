"""
Wraps query_incident.py's retrieval pipeline (Gemini embeddings + BigQuery
VECTOR_SEARCH) behind one small object, so a consumer doesn't need to know
these functions live in a file called query_incident.py, in this particular
project's folder, at all.

Every method here is a direct, unmodified delegation - no retrieval logic,
prompt text, or BigQuery SQL is duplicated in this file. See query_incident.py
for the real implementation.
"""


class IncidentSearch:
    """Similar-past-incident retrieval, the way Week 2's chat flow uses it."""

    def __init__(self):
        # imported inside __init__, not at module load time, so importing
        # aiops_sdk never requires GEMINI_API_KEY / ServiceNow env vars to be
        # set - only constructing IncidentSearch() does, exactly matching
        # when query_incident.py itself would first need them.
        import query_incident as _qi

        self._qi = _qi

    def embed(self, text: str):
        """Turn new incident text into a search vector."""
        return self._qi.embed_query(text)

    def search(self, query_vector, k: int = 5):
        """BigQuery VECTOR_SEARCH for the k closest past incidents."""
        return self._qi.search(query_vector, k=k)

    def find_similar(self, text: str, k: int = 5):
        """One call for the two steps app.py's api_query() always runs back
        to back on the human-in-the-loop path: embed, then search."""
        return self.search(self.embed(text), k=k)

    def build_prompt(self, text: str, matches):
        """Assemble the RAG prompt from retrieved past incidents."""
        return self._qi.build_prompt(text, matches)

    def corpus_size(self) -> int:
        """Live count of incidents currently in the BigQuery corpus."""
        return self._qi.corpus_size()

    def refresh_corpus(self) -> int:
        """Pull every resolved-or-closed incident back out of ServiceNow and
        rebuild the BigQuery corpus table from it, then hand off to
        build_bigquery_corpus.py's own, unmodified embed-and-load step -
        together, the same two-stage job run_refresh.py already chains
        (fetch_resolved_incidents.py, then build_bigquery_corpus.py) for its
        Cloud Run Job, wrapped as one call so a caller (aiops_console_app,
        after approving a fix) doesn't need to shell out to two scripts.

        The one deliberate difference from calling those two scripts
        as-is: the ServiceNow query below asks for `stateIN6,7` (Resolved
        OR Closed), not fetch_resolved_incidents.py's own `state=6`
        (Resolved only). Reason: this project already found, while fixing
        the "resolved candidates keep reappearing" bug, that on this
        instance `active` only goes false once an incident reaches Closed
        (state 7) - Resolved (state 6) alone doesn't do it. That strongly
        suggests incidents here do eventually transition from Resolved to
        Closed on their own (a standard ServiceNow auto-close job, or
        someone/something finishing the loop manually) - and once one does,
        fetch_resolved_incidents.py's own `state=6` filter would silently
        stop seeing it. That's a real, live explanation for a resolved
        incident's count *shrinking* over time even though nothing was
        deleted - the opposite of what a "grows as things get fixed" corpus
        should ever do. A Closed incident's close_notes are exactly as
        valid a past RCA as a Resolved one's, so there's no reason to
        exclude it here.

        Everything after the query is still exactly fetch_resolved_incidents.py's
        own to_corpus_records() (imported, not reimplemented) and
        build_bigquery_corpus.py's own main() (imported, not reimplemented,
        not modified) - only the query that decides which incidents are
        "in scope" changes, and only here.

        Two things worth knowing before calling this from a request handler:

        1. It's not incremental. build_bigquery_corpus.py always does a full
           WRITE_TRUNCATE reload of every incident this method hands it, not
           just the newest one - that's how that script already worked,
           unchanged here. So this re-embeds the whole corpus every time,
           and gets slower as more incidents pile up.
        2. It's not fast. Each record costs one paced Gemini embedding call
           (see build_bigquery_corpus.py's SECONDS_BETWEEN_CALLS) - at
           dozens of resolved incidents this can take a minute or more.
           Calling this inline on a Flask request thread would hold that
           request open the whole time; run it in a background thread and
           poll corpus_size() (or a status endpoint tracking this call)
           instead, which is what aiops_console_app does.
        """
        import json
        from pathlib import Path

        import requests
        import fetch_resolved_incidents as _fri
        import build_bigquery_corpus as _bbc

        resp = requests.get(
            f"{_fri.BASE_URL}/table/incident",
            auth=_fri.AUTH,
            headers=_fri.HEADERS,
            timeout=30,
            params={
                "sysparm_query": "stateIN6,7^ORDERBYDESCsys_updated_on",
                "sysparm_limit": 200,
                "sysparm_fields": "sys_id,number,short_description,category,priority,close_notes",
            },
        )
        resp.raise_for_status()
        incidents = resp.json()["result"]

        # to_corpus_records() is fetch_resolved_incidents.py's own, unmodified -
        # it already flattens to {incident_id, sys_id, short_description,
        # category, text} and already skips anything with no close_notes.
        records = _fri.to_corpus_records(incidents)
        Path(_bbc.CORPUS_FILE).write_text(json.dumps(records, indent=2))

        _bbc.main()  # embeds + WRITE_TRUNCATE-loads exactly what was just written, unmodified
        return self.corpus_size()

    def parse_slm_pick(self, suggestion: str, matches):
        """Which retrieved incident id the LLM's suggestion text actually
        picked - best-effort text parsing, same as query_incident.py's CLI
        flow uses right before showing a human the verdict prompt."""
        return self._qi.parse_slm_pick(suggestion, matches)

    def log_feedback(self, text: str, matches, slm_pick, verdict, log_file=None):
        """Record a human's verdict on a suggestion - confirmed / corrected
        / none_apply - to the same append-only judgment log
        query_incident.py's CLI flow writes after ask_human_verdict()."""
        kwargs = {}
        if log_file is not None:
            kwargs["log_file"] = log_file
        return self._qi.log_feedback(text, matches, slm_pick, verdict, **kwargs)
