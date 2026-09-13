"""
Structural + behavioral verification for aiops_sdk, run without any real
Gemini / BigQuery / ServiceNow credentials - every outbound call is mocked
at the same boundary functions the SDK itself delegates to (call_gemini,
embed_text, requests.post/requests.patch, get_caller_sys_id). This proves
the SDK's wrapper classes wire up correctly and delegate faithfully to the
existing, unmodified project code - it does not re-prove that
query_incident.py's BigQuery SQL or ServiceNow's API itself works, which
was already proven when the app was deployed.

This file is not part of the SDK - it's a one-off check. Run from scripts/:
    GEMINI_API_KEY=fake SN_INSTANCE=demo SN_USER=demo SN_PASS=demo \
        python3 aiops_sdk/examples/verify_sdk.py
(the env vars above are also set programmatically below so this also runs
with a plain `python3 aiops_sdk/examples/verify_sdk.py`)
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# make `scripts/` importable regardless of cwd - same trick app.py's own
# deployment relies on implicitly (Cloud Run sets the working directory)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# fake-but-present credentials, just enough for module-level `os.environ[...]`
# lookups (seed_resolved_incidents.py) to not raise KeyError at import time -
# every actual network call below is mocked, so these values are never used
# for anything real
os.environ.setdefault("SN_INSTANCE", "demo")
os.environ.setdefault("SN_USER", "demo")
os.environ.setdefault("SN_PASS", "demo")
os.environ.setdefault("GEMINI_API_KEY", "fake-key-for-structural-verification")

PASS = "PASS"
FAIL = "FAIL"
results = []


def check(name, fn):
    try:
        fn()
        results.append((PASS, name))
        print(f"[{PASS}] {name}")
    except Exception as exc:  # noqa: BLE001 - want every failure reported, not just the first
        results.append((FAIL, name))
        print(f"[{FAIL}] {name} - {exc!r}")


def main():
    from aiops_sdk import AiopsSDK, LLMProvider, GeminiProvider, IncidentSearch, Remediator, ServiceNowClient

    def _imports_cleanly():
        assert AiopsSDK and LLMProvider and GeminiProvider and IncidentSearch and Remediator and ServiceNowClient

    check("aiops_sdk imports cleanly, all 5 public names present", _imports_cleanly)

    def _sdk_constructs():
        sdk = AiopsSDK()
        assert isinstance(sdk.llm_provider, GeminiProvider)
        assert isinstance(sdk.search, IncidentSearch)
        assert isinstance(sdk.remediate, Remediator)
        assert isinstance(sdk.servicenow, ServiceNowClient)

    check("AiopsSDK() constructs with default GeminiProvider + all 3 sub-clients", _sdk_constructs)

    def _generate_delegates_to_call_gemini():
        # patch call_gemini exactly where GeminiProvider imports it from
        with patch("query_incident_gemini.call_gemini", return_value="mocked suggestion text") as mock_call:
            sdk = AiopsSDK()
            out = sdk.llm_provider.generate("some prompt")
            assert out == "mocked suggestion text"
            mock_call.assert_called_once()
            assert mock_call.call_args.args[0] == "some prompt" or mock_call.call_args.kwargs.get("prompt") is None

    check("GeminiProvider.generate() delegates to query_incident_gemini.call_gemini()", _generate_delegates_to_call_gemini)

    def _find_similar_delegates():
        fake_vector = [0.1, 0.2, 0.3]
        fake_matches = [{"incident_id": "INC0000001", "short_description": "mocked match", "text": "..."}]
        with patch("query_incident.embed_text", return_value=fake_vector) as mock_embed, \
             patch("query_incident.get_bq_client") as mock_bq:
            mock_client = MagicMock()
            mock_row = {"incident_id": "INC0000001", "sys_id": "abc", "short_description": "mocked match",
                        "category": "Software", "text": "...", "distance": 0.12}
            mock_client.query.return_value.result.return_value = [mock_row]
            mock_client.project = "fake-project"
            mock_bq.return_value = mock_client

            sdk = AiopsSDK()
            matches = sdk.search.find_similar("upload fails with a permission error")
            assert matches[0]["incident_id"] == "INC0000001"
            assert mock_embed.called

    check("IncidentSearch.find_similar() delegates embed()+search() to query_incident.py", _find_similar_delegates)

    def _refresh_corpus_includes_resolved_and_closed_incidents():
        # Regression test for the corpus count dropping unexpectedly after
        # refresh_corpus() started actually being called (14 instead of the
        # ~52 originally seeded). fetch_resolved_incidents.py's own query
        # only looks at state=6 (Resolved) - but this project already found
        # `active` on this instance only goes false at Closed (state 7), not
        # Resolved, which means incidents here really do age from Resolved
        # into Closed on their own. A state=6-only query would silently lose
        # every incident that's aged into Closed since it was last resolved -
        # refresh_corpus() must ask for stateIN6,7 instead, so a Closed
        # incident's close_notes stay part of the corpus rather than
        # quietly disappearing from it.
        import json
        from pathlib import Path

        incidents = [
            {"sys_id": "s1", "number": "INC1", "short_description": "Login broken", "category": "Software",
             "priority": "3", "close_notes": "Root Cause:\nx\n\nResolution:\ny", "state": "6"},
            {"sys_id": "s2", "number": "INC2", "short_description": "Upload broken", "category": "Software",
             "priority": "3", "close_notes": "Root Cause:\nx\n\nResolution:\ny", "state": "7"},
        ]
        corpus_path = Path("resolved_incidents_corpus.json")
        try:
            with patch("requests.get") as mock_get, \
                 patch("build_bigquery_corpus.main") as mock_build, \
                 patch("query_incident.corpus_size", return_value=2) as mock_size:
                fake_resp = MagicMock()
                fake_resp.raise_for_status.return_value = None
                fake_resp.json.return_value = {"result": incidents}
                mock_get.return_value = fake_resp

                sdk = AiopsSDK()
                size = sdk.search.refresh_corpus()

                called_query = mock_get.call_args.kwargs["params"]["sysparm_query"]
                assert "stateIN6,7" in called_query, (
                    "refresh_corpus() must query stateIN6,7 (Resolved OR Closed), "
                    "not just Resolved - otherwise incidents that have aged into "
                    "Closed silently drop out of the corpus"
                )
                assert mock_build.called, "must still hand off to build_bigquery_corpus.py's own embed+load step, unmodified"
                assert mock_size.called
                assert size == 2

            written = json.loads(corpus_path.read_text())
            assert {r["incident_id"] for r in written} == {"INC1", "INC2"}, (
                "both the Resolved and the Closed incident must make it into the corpus file"
            )
        finally:
            if corpus_path.exists():
                corpus_path.unlink()

    check(
        "IncidentSearch.refresh_corpus() includes Closed incidents, not just Resolved, so the corpus can't shrink as incidents age",
        _refresh_corpus_includes_resolved_and_closed_incidents,
    )

    def _suggest_composes_search_and_llm():
        with patch("query_incident.embed_text", return_value=[0.1, 0.2]), \
             patch("query_incident.get_bq_client") as mock_bq, \
             patch("query_incident_gemini.call_gemini", return_value="Best match is INC0000001 because...") as mock_call:
            mock_client = MagicMock()
            mock_client.query.return_value.result.return_value = [
                {"incident_id": "INC0000001", "sys_id": "x", "short_description": "s", "category": "c",
                 "text": "past incident text", "distance": 0.1}
            ]
            mock_client.project = "fake-project"
            mock_bq.return_value = mock_client

            sdk = AiopsSDK()
            result = sdk.suggest("a brand new incident")
            assert "INC0000001" in result["suggestion"]
            assert result["matches"][0]["incident_id"] == "INC0000001"
            assert mock_call.called

    check("AiopsSDK.suggest() composes IncidentSearch + LLMProvider (Week 2 flow)", _suggest_composes_search_and_llm)

    def _auto_remediate_resolves():
        # end-to-end HIL-off flow through the real week3.auto_remediation module,
        # with only the network edges mocked (embedding call + ServiceNow HTTP).
        # Patched at week3.auto_remediation's own names, not query_incident's /
        # seed_resolved_incidents' - auto_remediation.py did
        # `from query_incident import embed_text` and
        # `from seed_resolved_incidents import ... get_caller_sys_id`, which binds
        # its own local references at import time; patching the origin module's
        # attribute wouldn't reach those already-bound names.
        fake_response = MagicMock()
        fake_response.ok = True
        fake_response.json.return_value = {"result": {"sys_id": "sys123", "number": "INC1000042"}}

        with patch("week3.auto_remediation.embed_text", return_value=[1.0, 0.0, 0.0]), \
             patch("week3.auto_remediation.get_caller_sys_id", return_value="caller-sys-id"), \
             patch("requests.post", return_value=fake_response), \
             patch("requests.patch", return_value=fake_response):
            sdk = AiopsSDK()

            # force a confident match regardless of the mocked embedding above,
            # by matching the query text to the catalog's own description
            entry = sdk.remediate.catalog[0]
            outcome = sdk.auto_remediate(entry["short_description"])

            assert outcome["outcome"] in ("auto_resolved", "escalated")
            if outcome["outcome"] == "auto_resolved":
                assert outcome["incident_number"] == "INC1000042"

    check("AiopsSDK.auto_remediate() runs the real match+fix+ServiceNow flow (Week 3)", _auto_remediate_resolves)

    def _propose_never_runs_a_fix():
        # the entire point of propose_remedy() is that it CANNOT execute a
        # remedy - prove that directly by patching every remedy function in
        # the real catalog to explode if called, then confirm propose()
        # still returns a clean result without tripping any of them.
        from week3 import remedies as _remedies

        with patch("week3.auto_remediation.embed_text", return_value=[1.0, 0.0, 0.0]), \
             patch.object(_remedies, "fix_app_down", side_effect=AssertionError("fix ran without approval!")), \
             patch.object(_remedies, "fix_login_ui_disabled", side_effect=AssertionError("fix ran without approval!")), \
             patch.object(_remedies, "fix_login_cred_backend", side_effect=AssertionError("fix ran without approval!")), \
             patch.object(_remedies, "fix_upload_permission", side_effect=AssertionError("fix ran without approval!")):
            sdk = AiopsSDK()
            entry = sdk.remediate.catalog[0]
            result = sdk.propose_remedy(entry["short_description"])

            assert result["decision"] == "needs_human_approval"
            assert result["issue_id"] == entry["issue_id"]
            assert "similarity" in result
            # if any patched remedy had been called, the AssertionError above
            # would have propagated out of match_auto_fix() and failed this
            # check already - reaching here IS the proof nothing fired.

    check("propose_remedy() matches only - never calls a remedy function", _propose_never_runs_a_fix)

    def _run_confirmed_remedy_executes_and_records_approval():
        fake_response = MagicMock()
        fake_response.ok = True
        fake_response.json.return_value = {"result": {"sys_id": "sys999", "number": "INC2000099"}}

        with patch("seed_resolved_incidents.get_caller_sys_id", return_value="caller-sys-id"), \
             patch("requests.post", return_value=fake_response) as mock_post, \
             patch("requests.patch", return_value=fake_response) as mock_patch:
            sdk = AiopsSDK()
            entry = sdk.remediate.catalog[0]

            outcome = sdk.run_confirmed_remedy(
                entry["short_description"], entry["issue_id"], approved_by="ramesh"
            )

            assert outcome["outcome"] == "resolved_with_approval"
            assert outcome["incident_number"] == "INC2000099"
            assert outcome["approved_by"] == "ramesh"
            assert mock_post.called and mock_patch.called
            # the create call's payload is where the approval note lives -
            # confirm it actually made it into what gets written to ServiceNow
            created_payload = mock_post.call_args.kwargs.get("json", {})
            assert "approved by ramesh" in created_payload.get("description", "")

    check("run_confirmed_remedy() runs the real fix and records who approved it", _run_confirmed_remedy_executes_and_records_approval)

    def _raise_remedy_candidate_never_runs_a_fix():
        # The split-audience flow's entire safety property: an end-user app
        # calling raise_remedy_candidate() must be physically incapable of
        # running a remedy, even on a confident match - proved the same way
        # propose_remedy() was proved, by making every remedy function
        # explode if called.
        from week3 import remedies as _remedies

        fake_response = MagicMock()
        fake_response.ok = True
        fake_response.json.return_value = {"result": {"sys_id": "sys555", "number": "INC6000055"}}

        with patch("week3.auto_remediation.embed_text", return_value=[1.0, 0.0, 0.0]), \
             patch("seed_resolved_incidents.get_caller_sys_id", return_value="caller-sys-id"), \
             patch("requests.post", return_value=fake_response) as mock_post, \
             patch.object(_remedies, "fix_app_down", side_effect=AssertionError("fix ran without approval!")), \
             patch.object(_remedies, "fix_login_ui_disabled", side_effect=AssertionError("fix ran without approval!")), \
             patch.object(_remedies, "fix_login_cred_backend", side_effect=AssertionError("fix ran without approval!")), \
             patch.object(_remedies, "fix_upload_permission", side_effect=AssertionError("fix ran without approval!")):
            sdk = AiopsSDK()
            entry = sdk.remediate.catalog[0]
            result = sdk.raise_remedy_candidate(entry["short_description"])

            assert result["decision"] == "raised_for_aiops_review"
            assert result["issue_id"] == entry["issue_id"]
            assert result["incident_number"] == "INC6000055"
            # the incident really was tagged for AIOps review in its
            # description (not a custom category, which can get silently
            # rejected by ServiceNow) - confirm the actual payload sent, not
            # just the return value
            created_payload = mock_post.call_args.kwargs.get("json", {})
            assert created_payload.get("category") == "Software"  # a real, valid category
            assert "[AIOps Auto-Remediation Candidate]" in created_payload.get("description", "")
            assert entry["issue_id"] in created_payload.get("description", "")

    check("raise_remedy_candidate() opens a tagged incident, never runs a fix", _raise_remedy_candidate_never_runs_a_fix)

    def _list_remedy_candidates_parses_servicenow():
        # list_remedy_candidates() must read real, unresolved ServiceNow
        # incidents (mocked here) back into the shape aiops_console_app
        # renders - proving the parser round-trips what
        # raise_remedy_candidate() actually writes.
        mock_incident = {
            "sys_id": "sys555",
            "number": "INC6000055",
            "short_description": "Upload fails with a permission error",
            "description": (
                "[AIOps Auto-Remediation Candidate]\n"
                "Matched issue: upload_permission (similarity 0.812)\n"
                "Waiting for an AIOps team member to review and approve.\n\n"
                "Original report:\nUpload fails with a permission error"
            ),
            "opened_at": "2026-09-13 10:00:00",
        }
        with patch("requests.get") as mock_get:
            fake_response = MagicMock()
            fake_response.ok = True
            fake_response.json.return_value = {"result": [mock_incident]}
            mock_get.return_value = fake_response

            sdk = AiopsSDK()
            candidates = sdk.list_remedy_candidates()

            assert len(candidates) == 1
            c = candidates[0]
            assert c["number"] == "INC6000055"
            assert c["issue_id"] == "upload_permission"
            assert c["similarity"] == 0.812
            assert c["incident_text"] == "Upload fails with a permission error"
            assert "chmod" in c["remedy_explanation"]
            # confirm it actually asked ServiceNow to filter on the
            # description tag (not category, which can be silently
            # rejected) and on active status, not just returning whatever
            # came back
            called_query = mock_get.call_args.kwargs["params"]["sysparm_query"]
            assert "active=true" in called_query
            assert "stateNOT IN6,7" in called_query
            assert "AIOps Auto-Remediation Candidate" in called_query

    check("list_remedy_candidates() parses a real ServiceNow response into the console's shape", _list_remedy_candidates_parses_servicenow)

    def _list_remedy_candidates_excludes_already_resolved():
        # Regression test for the bug where a candidate the AIOps console
        # had already approved kept reappearing in the "needs review" list.
        # Root cause: stock ServiceNow only flips `active` to false once an
        # incident reaches Closed (state 7) - a merely Resolved (state 6)
        # incident, which is all run_confirmed_fix_for_candidate() ever
        # sets, can stay `active=true` on some instances. So this asserts
        # the resolved incident is excluded even when the (mocked)
        # ServiceNow response returns it anyway, i.e. even if the
        # active=true^stateNOT IN6,7 query filter didn't do its job on a
        # given instance, the client-side check in list_remedy_candidates()
        # still has to catch it.
        still_open = {
            "sys_id": "sys777",
            "number": "INC6000077",
            "state": "2",  # In Progress - genuinely still open
            "description": (
                "[AIOps Auto-Remediation Candidate]\n"
                "Matched issue: login_ui_disabled (similarity 0.874)\n\n"
                "Original report:\nThe Sign In button is disabled"
            ),
            "opened_at": "2026-09-13 10:00:00",
        }
        already_resolved = {
            "sys_id": "sys778",
            "number": "INC6000078",
            "state": "6",  # Resolved - already approved and fixed once
            "description": (
                "[AIOps Auto-Remediation Candidate]\n"
                "Matched issue: login_ui_disabled (similarity 0.874)\n\n"
                "Original report:\nThe Sign In button is disabled"
            ),
            "opened_at": "2026-09-13 09:00:00",
        }
        with patch("requests.get") as mock_get:
            fake_response = MagicMock()
            fake_response.ok = True
            # Simulate the exact failure mode reported: ServiceNow's
            # active=true^stateNOT IN6,7 filter still hands back a resolved
            # incident anyway (e.g. an instance where `active` never
            # tracked `state` correctly). list_remedy_candidates() must not
            # trust that and show it as needing review again.
            fake_response.json.return_value = {"result": [still_open, already_resolved]}
            mock_get.return_value = fake_response

            sdk = AiopsSDK()
            candidates = sdk.list_remedy_candidates()

            numbers = [c["number"] for c in candidates]
            assert "INC6000077" in numbers
            assert "INC6000078" not in numbers, (
                "an already-resolved incident (state 6) reappeared as a candidate needing review"
            )

    check(
        "list_remedy_candidates() never re-shows an already-resolved incident, even if ServiceNow's own filter didn't exclude it",
        _list_remedy_candidates_excludes_already_resolved,
    )

    def _run_confirmed_remedy_for_candidate_resolves_same_incident():
        # The AIOps side's approval step must resolve the SAME incident
        # raise_remedy_candidate() opened (by sys_id), not create a new one -
        # that's what makes it "the same ticket the end user is watching."
        fake_response = MagicMock()
        fake_response.ok = True
        fake_response.json.return_value = {"result": {"sys_id": "sys555", "number": "INC6000055"}}

        with patch("requests.patch", return_value=fake_response) as mock_patch, \
             patch("requests.post") as mock_post:
            sdk = AiopsSDK()
            entry = sdk.remediate.catalog[0]

            outcome = sdk.run_confirmed_remedy_for_candidate(
                "sys555", entry["short_description"], entry["issue_id"], approved_by="priya (aiops)",
            )

            assert outcome["outcome"] == "resolved_with_approval"
            assert outcome["incident_number"] == "INC6000055"
            assert outcome["approved_by"] == "priya (aiops)"
            assert not mock_post.called  # no new incident was created
            assert mock_patch.called
            # confirms it patched the exact sys_id it was given, not a new one
            patched_url = mock_patch.call_args.args[0] if mock_patch.call_args.args else mock_patch.call_args.kwargs.get("url", "")
            assert "sys555" in patched_url

    check("run_confirmed_remedy_for_candidate() resolves the same incident, creates no new one", _run_confirmed_remedy_for_candidate_resolves_same_incident)

    def _pluggable_llm_swap():
        class StubProvider(LLMProvider):
            def generate(self, prompt: str) -> str:
                return f"stub response to: {prompt[:20]}"

        sdk = AiopsSDK(llm_provider=StubProvider())
        assert isinstance(sdk.llm_provider, StubProvider)
        assert sdk.llm_provider.generate("hello").startswith("stub response")

    check("A custom LLMProvider can be swapped in via the constructor", _pluggable_llm_swap)

    failed = [name for status, name in results if status == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    if failed:
        print("Failed:")
        for name in failed:
            print(f"  - {name}")
        sys.exit(1)


if __name__ == "__main__":
    main()
