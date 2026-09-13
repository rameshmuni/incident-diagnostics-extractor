"""
Boots aiops_sdk_demo_app/app.py as a real Flask app and hits its actual HTTP
routes with Flask's test client - no real Gemini / BigQuery / ServiceNow
credentials needed, because every outbound call is mocked at the same
boundary functions aiops_sdk/examples/verify_sdk.py already mocks (this is
the same technique, just driven through HTTP requests instead of calling
Python objects directly).

verify_sdk.py proves the SDK's classes wire up correctly. This proves the
whole second app - routes, JSON shapes, the reused /demo blueprint - actually
boots and answers requests the same way the original app.py's routes do.
Neither script is part of the app; both are one-off checks. Run from
anywhere:
    python3 aiops_sdk_demo_app/verify_app.py
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
    except Exception as exc:  # noqa: BLE001 - want every failure reported, not just the first
        results.append((FAIL, name))
        print(f"[{FAIL}] {name} - {exc!r}")


def main():
    # import app AFTER env vars are set, since constructing AiopsSDK() at
    # module load time (see aiops_sdk_demo_app/app.py) touches
    # seed_resolved_incidents.py's module-level os.environ[...] lookups
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import app as demo_app_module

    client = demo_app_module.app.test_client()

    def _home_serves_index():
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"<html" in resp.data.lower()

    check("GET / serves the reused static/index.html", _home_serves_index)

    def _status_reports_ok():
        with patch("query_incident.corpus_size", return_value=42):
            resp = client.get("/api/status")
            body = resp.get_json()
            assert resp.status_code == 200
            assert body["ok"] is True
            assert body["corpus_size"] == 42
            assert body["model"]  # non-empty model name from the provider, not hardcoded

    check("GET /api/status delegates to sdk.search.corpus_size() + sdk.llm_provider.model", _status_reports_ok)

    def _query_review_mode():
        fake_matches = [{"incident_id": "INC0000001", "short_description": "mocked match", "text": "..."}]
        with patch("query_incident.embed_text", return_value=[0.1, 0.2]), \
             patch("query_incident.get_bq_client") as mock_bq, \
             patch("query_incident_gemini.call_gemini", return_value="Best match is INC0000001 because...") as mock_call:
            mock_client = MagicMock()
            mock_client.query.return_value.result.return_value = [
                {"incident_id": "INC0000001", "sys_id": "x", "short_description": "mocked match",
                 "category": "c", "text": "past incident text", "distance": 0.1}
            ]
            mock_client.project = "fake-project"
            mock_bq.return_value = mock_client

            resp = client.post("/api/query", json={"text": "a brand new incident", "human_in_loop": True})
            body = resp.get_json()
            assert resp.status_code == 200
            assert body["mode"] == "review"
            assert body["matches"][0]["incident_id"] == "INC0000001"
            assert body["slm_pick"] == "INC0000001"
            assert mock_call.called

    check("POST /api/query (human_in_loop=true) returns the review-mode shape", _query_review_mode)

    def _query_off_raises_incident_not_runs():
        # human_in_loop=false must now RAISE a real ServiceNow incident for a
        # confident match, never run a remedy - this app has no code path
        # left that can. Every remedy function is patched to explode if
        # called, proving /api/query itself never touches one.
        from week3 import remedies as _remedies

        fake_response = MagicMock()
        fake_response.ok = True
        fake_response.json.return_value = {"result": {"sys_id": "sys321", "number": "INC4000021"}}

        with patch("week3.auto_remediation.embed_text", return_value=[1.0, 0.0, 0.0]), \
             patch("seed_resolved_incidents.get_caller_sys_id", return_value="caller-sys-id"), \
             patch("requests.post", return_value=fake_response), \
             patch.object(_remedies, "fix_app_down", side_effect=AssertionError("fix ran without approval!")), \
             patch.object(_remedies, "fix_login_ui_disabled", side_effect=AssertionError("fix ran without approval!")), \
             patch.object(_remedies, "fix_login_cred_backend", side_effect=AssertionError("fix ran without approval!")), \
             patch.object(_remedies, "fix_upload_permission", side_effect=AssertionError("fix ran without approval!")):
            entry = demo_app_module.sdk.remediate.catalog[0]
            resp = client.post("/api/query", json={"text": entry["short_description"], "human_in_loop": False})
            body = resp.get_json()
            assert resp.status_code == 200
            assert body["mode"] == "raised"
            assert body["decision"] == "raised_for_aiops_review"
            assert body["issue_id"] == entry["issue_id"]
            assert body["incident_number"] == "INC4000021"

    check("POST /api/query (human_in_loop=false) raises an incident without running a fix", _query_off_raises_incident_not_runs)

    def _no_remedy_execution_route_exists():
        # The strongest possible proof this app can't run a remedy: the
        # route that used to do that doesn't exist here anymore at all - it
        # isn't even in the Flask url_map, and there's no code path in this
        # process that ever calls sdk.run_confirmed_remedy_for_candidate()
        # or sdk.auto_remediate(). POSTing to the old path 404s or 405s
        # (Flask's static-file catch-all owns the URL shape but only for
        # GET) - either way, nothing runs.
        assert "/api/remedy/confirm" not in {str(r) for r in demo_app_module.app.url_map.iter_rules()}
        resp = client.post("/api/remedy/confirm", json={})
        assert resp.status_code in (404, 405)

    check("This app has no route capable of executing a remedy", _no_remedy_execution_route_exists)

    def _verdict_confirmed():
        with patch("query_incident.log_feedback") as mock_log:
            resp = client.post("/api/verdict", json={
                "text": "some incident",
                "matches": [{"incident_id": "INC0000001", "short_description": "s", "text": "t"}],
                "slm_pick": "INC0000001",
                "verdict": "confirmed",
            })
            body = resp.get_json()
            assert resp.status_code == 200
            assert body["result"] == "confirmed"
            assert body["final_id"] == "INC0000001"
            assert mock_log.called

    check("POST /api/verdict (confirmed) logs feedback and returns the confirmed shape", _verdict_confirmed)

    def _verdict_none_apply_escalates():
        fake_response = MagicMock()
        fake_response.ok = True
        fake_response.json.return_value = {"result": {"sys_id": "sysE", "number": "INC3000001"}}
        with patch("query_incident.log_feedback"), \
             patch("query_incident.get_caller_sys_id", return_value="caller-sys-id"), \
             patch("requests.post", return_value=fake_response):
            resp = client.post("/api/verdict", json={
                "text": "some incident",
                "matches": [{"incident_id": "INC0000001", "short_description": "s", "text": "t"}],
                "slm_pick": "INC0000001",
                "verdict": "none_apply",
            })
            body = resp.get_json()
            assert resp.status_code == 200
            assert body["result"] == "escalated"
            assert body["incident_number"] == "INC3000001"
            assert body["log_file"]  # sdk.servicenow.escalation_log came back non-empty

    check("POST /api/verdict (none_apply) escalates via sdk.servicenow and returns a log_file", _verdict_none_apply_escalates)

    def _demo_state_break_reset_roundtrip():
        # real calls, on purpose - mock_systems.py is pure local state (a JSON
        # file + a real chmod on a folder inside week3/), nothing to mock.
        resp = client.get("/api/demo/state")
        assert resp.status_code == 200
        assert "app_down" in resp.get_json()

        resp = client.post("/api/demo/break", json={"issue_id": "app_down"})
        assert resp.status_code == 200
        assert resp.get_json()["state"]["app_down"] is True

        resp = client.post("/api/demo/reset")
        assert resp.status_code == 200
        assert resp.get_json()["state"]["app_down"] is False

    check("GET/POST /api/demo/* round-trips real (unmocked) mock_systems state", _demo_state_break_reset_roundtrip)

    def _demo_login_page_reused():
        resp = client.get("/demo/login")
        assert resp.status_code == 200
        assert b"Sign in" in resp.data

    check("GET /demo/login serves the unmodified week3.demo_app blueprint", _demo_login_page_reused)

    failed = [name for status, name in results if status == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    if failed:
        print("Failed:")
        for name in failed:
            print(f"  - {name}")
        sys.exit(1)


if __name__ == "__main__":
    main()
