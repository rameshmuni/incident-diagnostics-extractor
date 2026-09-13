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
        """Pull every resolved-or-closed incident out of ServiceNow and add
        whichever ones aren't already in the BigQuery corpus yet - an
        incremental top-up, not a full rebuild.

        The ServiceNow query below asks for `stateIN6,7` (Resolved OR
        Closed), not fetch_resolved_incidents.py's own `state=6` (Resolved
        only). Reason: this project already found, while fixing the
        "resolved candidates keep reappearing" bug, that on this instance
        `active` only goes false once an incident reaches Closed (state 7) -
        Resolved (state 6) alone doesn't do it. That strongly suggests
        incidents here do eventually transition from Resolved to Closed on
        their own (a standard ServiceNow auto-close job, or someone/
        something finishing the loop manually) - and once one does, a
        `state=6`-only query would silently stop seeing it. A Closed
        incident's close_notes are exactly as valid a past RCA as a
        Resolved one's, so there's no reason to exclude it here.

        This method used to hand every incident it found straight to
        build_bigquery_corpus.py's main(), which always does a full
        WRITE_TRUNCATE reload: re-fetch everything, re-embed everything,
        replace the whole table - correct, but wasteful. It re-embedded
        incidents that were already embedded in a previous refresh, every
        single time, so the whole thing got slower the more incidents
        piled up even though the actual NEW information (whatever just got
        resolved) is normally a tiny fraction of the total corpus - one
        incident's fix shouldn't cost re-embedding every other incident
        that was already searchable.

        This version instead:

        1. Reads which `sys_id`s are already sitting in the BigQuery table
           (one plain SELECT, no embedding calls involved at all).
        2. Fetches the same `stateIN6,7` incidents from ServiceNow as
           before, via fetch_resolved_incidents.py's own to_corpus_records()
           (imported, not reimplemented).
        3. Skips anything whose `sys_id` is already in the table - only
           incidents this method has genuinely never seen before proceed.
        4. Embeds just those new ones (still one paced Gemini call per
           record, reusing build_bigquery_corpus.py's own
           SECONDS_BETWEEN_CALLS pacing and SCHEMA constants, unmodified)
           and WRITE_APPENDs just those new rows onto the existing table -
           the rows already there are never re-read, re-embedded, or
           rewritten.

        Net effect: if nothing new resolved since the last refresh, this
        returns almost immediately - one BigQuery SELECT, zero embedding
        calls. If N incidents newly resolved, this costs N paced embedding
        calls, not (everything-so-far + N). The very first time this runs
        against a brand-new project (no table yet), every incident is "new"
        by definition, so that one run is still a full embed of everything -
        exactly as slow as the old approach, but only ever once, not on
        every single approval from then on.

        One accepted tradeoff: if an already-embedded incident's
        close_notes or short_description were edited AFTER it was embedded,
        this method won't notice or re-embed it - it only checks whether the
        `sys_id` is new, not whether the text changed. On this instance,
        close_notes don't get rewritten after an incident is
        Resolved/Closed, so this hasn't been observed to matter in
        practice - but it's a real, deliberate difference from the old
        always-re-embed-everything behavior, worth knowing if that
        assumption ever stops holding.
        """
        import time

        import requests
        import fetch_resolved_incidents as _fri
        import build_bigquery_corpus as _bbc
        from google.cloud import bigquery

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

        client = self._qi.get_bq_client()
        table_id = self._qi.table_ref(client)

        try:
            existing_sys_ids = {
                row["sys_id"] for row in client.query(f"SELECT sys_id FROM `{table_id}`").result()
            }
        except Exception:
            # No table yet - the very first refresh ever against this
            # project, so nothing's been embedded before. Treat that as an
            # empty corpus rather than letting a NotFound here look like a
            # real failure.
            existing_sys_ids = set()

        new_records = [r for r in records if r["sys_id"] not in existing_sys_ids]

        if new_records:
            for i, record in enumerate(new_records, start=1):
                record["embedding"] = self._qi.embed_text(record["text"], task_type="RETRIEVAL_DOCUMENT")
                if i < len(new_records):
                    time.sleep(_bbc.SECONDS_BETWEEN_CALLS)

            client.create_dataset(bigquery.DatasetReference(client.project, self._qi.BQ_DATASET), exists_ok=True)
            job_config = bigquery.LoadJobConfig(
                schema=_bbc.SCHEMA,
                # APPEND, not TRUNCATE: this is the whole point of the
                # rewrite above - only the newly-embedded rows get added,
                # everything already in the table stays untouched.
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            )
            load_job = client.load_table_from_json(new_records, table_id, job_config=job_config)
            load_job.result()

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
