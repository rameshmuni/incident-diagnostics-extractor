"""
Boots aiops_console_app/app.py as a real Flask app and hits its actual HTTP
routes with Flask's test client - same technique as
aiops_sdk_demo_app/verify_app.py, no real Gemini / BigQuery / ServiceNow
credentials needed. Run from anywhere:
    python3 aiops_console_app/verify_console_app.py
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
    except Exception as exc:  # noqa: BLE001
        results.append((FAIL, name))
        print(f"[{FAIL}] {name} - {exc!r}")


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import app as console_app_module

    client = console_app_module.app.test_client()

    def _home_serves_index():
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"AIOps Console" in resp.data

    check("GET / serves the console's own index.html", _home_serves_index)

    def _list_candidates_parses_servicenow():
        mock_incident = {
            "sys_id": "sys888",
            "number": "INC7000088",
            "short_description": "Upload fails with a permission error",
            "description": (
                "[AIOps Auto-Remediation Candidate]\n"
                "Matched issue: upload_permission (similarity 0.79)\n"
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

            resp = client.get("/api/candidates")
            body = resp.get_json()
            assert resp.status_code == 200
            assert body["ok"] is True
            assert len(body["candidates"]) == 1
            c = body["candidates"][0]
            assert c["number"] == "INC7000088"
            assert c["issue_id"] == "upload_permission"
            assert "chmod" in c["remedy_explanation"]

    check("GET /api/candidates lists open incidents from ServiceNow", _list_candidates_parses_servicenow)

    def _confirm_runs_fix_and_resolves_same_incident():
        fake_response = MagicMock()
        fake_response.ok = True
        fake_response.json.return_value = {"result": {"sys_id": "sys888", "number": "INC7000088"}}

        # _start_corpus_refresh() spawns a real background thread that would
        # otherwise make real ServiceNow/Gemini/BigQuery calls with the fake
        # "demo" credentials this file sets - mocked here so this test only
        # proves what it's meant to prove (the fix ran, the same incident
        # got resolved). The background-refresh trigger itself is proven by
        # the dedicated checks below, without any real thread involved.
        with patch("requests.patch", return_value=fake_response) as mock_patch, \
             patch("requests.post") as mock_post, \
             patch.object(console_app_module, "_start_corpus_refresh", return_value="started") as mock_trigger:
            entry = console_app_module.sdk.remediate.catalog[3]  # upload_permission
            resp = client.post("/api/candidates/confirm", json={
                "sys_id": "sys888",
                "incident_text": entry["short_description"],
                "issue_id": entry["issue_id"],
                "approved_by": "priya (aiops)",
            })
            body = resp.get_json()
            assert resp.status_code == 200
            assert body["ok"] is True
            assert body["outcome"] == "resolved_with_approval"
            assert body["incident_number"] == "INC7000088"
            assert body["approved_by"] == "priya (aiops)"
            assert not mock_post.called  # no new incident created
            assert mock_patch.called
            # A resolved incident must trigger the corpus rebuild that
            # keeps Week 2's retrieval (and the chat app's resolved-incident
            # count) from silently going stale - see the "why" note in
            # aiops_sdk/retrieval.py's refresh_corpus().
            assert mock_trigger.called
            assert body["corpus_refresh"] == "started"

    check("POST /api/candidates/confirm runs the fix and resolves the same incident, and triggers a corpus refresh", _confirm_runs_fix_and_resolves_same_incident)

    def _corpus_refresh_runs_in_background_and_updates_status():
        # Calls the background-thread target directly (not via a real
        # thread) so this test is deterministic - it's proving
        # _run_corpus_refresh()'s state transitions, not testing Python's
        # threading module.
        console_app_module._refresh_state.update(
            status="idle", corpus_size=None, started_at=None, finished_at=None, error=None,
        )
        with patch.object(console_app_module.sdk.search, "refresh_corpus", return_value=57) as mock_refresh:
            console_app_module._run_corpus_refresh()
            assert mock_refresh.called

        resp = client.get("/api/refresh_status")
        body = resp.get_json()
        assert body["ok"] is True
        assert body["status"] == "done"
        assert body["corpus_size"] == 57
        assert body["error"] is None

    check("_run_corpus_refresh() calls sdk.search.refresh_corpus() and /api/refresh_status reports the result", _corpus_refresh_runs_in_background_and_updates_status)

    def _corpus_refresh_reports_failure_without_looking_like_the_fix_failed():
        console_app_module._refresh_state.update(
            status="idle", corpus_size=None, started_at=None, finished_at=None, error=None,
        )
        with patch.object(console_app_module.sdk.search, "refresh_corpus", side_effect=RuntimeError("BigQuery is unreachable")):
            console_app_module._run_corpus_refresh()  # must not raise out of this function

        resp = client.get("/api/refresh_status")
        body = resp.get_json()
        assert body["status"] == "error"
        assert "BigQuery is unreachable" in body["error"]

    check("A failed corpus refresh is reported via /api/refresh_status without raising", _corpus_refresh_reports_failure_without_looking_like_the_fix_failed)

    def _manual_refresh_endpoint_triggers_a_refresh_without_approving_anything():
        # Regression coverage for needing to force/test a corpus rebuild
        # against whatever ServiceNow currently has, without waiting for a
        # new candidate to approve - the gap that made a real fix to
        # refresh_corpus() look like it hadn't taken effect, when really no
        # rebuild had run since the fix was deployed.
        console_app_module._refresh_state.update(status="idle")
        with patch.object(console_app_module, "_start_corpus_refresh", return_value="started") as mock_trigger:
            resp = client.post("/api/refresh_corpus")
            body = resp.get_json()
            assert resp.status_code == 200
            assert body["ok"] is True
            assert body["corpus_refresh"] == "started"
            assert mock_trigger.called

    check("POST /api/refresh_corpus triggers a corpus rebuild on its own, with no incident to approve", _manual_refresh_endpoint_triggers_a_refresh_without_approving_anything)

    def _start_corpus_refresh_does_not_pile_up_threads():
        console_app_module._refresh_state.update(status="running")
        with patch("threading.Thread") as mock_thread:
            result = console_app_module._start_corpus_refresh()
            assert result == "already_running"
            assert not mock_thread.called

        console_app_module._refresh_state.update(status="idle")

    check("_start_corpus_refresh() won't start a second refresh while one is already running", _start_corpus_refresh_does_not_pile_up_threads)

    def _confirm_rejects_unknown_issue_id():
        resp = client.post("/api/candidates/confirm", json={
            "sys_id": "sys999",
            "incident_text": "something",
            "issue_id": "not_a_real_issue",
        })
        body = resp.get_json()
        assert resp.status_code == 500
        assert body["ok"] is False
        assert "unknown issue_id" in body["error"]

    check("POST /api/candidates/confirm rejects an unknown issue_id instead of guessing", _confirm_rejects_unknown_issue_id)

    failed = [name for status, name in results if status == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    if failed:
        print("Failed:")
        for name in failed:
            print(f"  - {name}")
        sys.exit(1)


if __name__ == "__main__":
    main()
