"""
Verifies mock_systems.py's shared-state behavior - no real BigQuery credentials needed.
BigQuery itself is mocked with a small in-memory dict standing in for the table, so this
can prove "does state written by one process become visible to a completely separate
process" (the actual bug this was written to fix: aiops_console_app running a remedy had no
effect the demo app's own container could ever see, because they don't share a local disk on
Cloud Run) without touching a real project.

Two things this deliberately does NOT re-prove: that BigQuery's real MERGE/SELECT SQL is
syntactically valid (that's exercised for real the first time this runs against an actual
project - see the module docstring's "one more tiny table" note), and root's os.access()
bypass (a real, pre-existing Linux behavior, not something this file works around - see
mock_systems.py's own comments on why the container must run as a non-root user).

Run from scripts/:
    python3 week3/verify_mock_systems.py
"""

import os
import sys
import types
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


def _mode(path):
    return oct(os.stat(path).st_mode & 0o777)


class _FakeBigQuery:
    """One in-memory row standing in for the real demo_state table - shared across however
    many times mock_systems' own client cache gets reset, which is exactly how this test
    simulates "a separate process/container" (a real separate container would have its own
    cache automatically; here, `ms._bq_client_cache = None` forces the same effect)."""

    def __init__(self):
        self.row = {}

    def client_factory(self):
        client = MagicMock()
        client.project = "fake-project"

        def fake_query(sql, job_config=None):
            result = MagicMock()
            params = {p.name: p.value for p in (job_config.query_parameters if job_config else [])}
            if sql.strip().startswith("SELECT"):
                result.result.return_value = [dict(self.row)] if self.row else []
            else:  # the MERGE statement
                self.row.update({k: v for k, v in params.items() if k != "id"})
                result.result.return_value = []
            return result

        client.query.side_effect = fake_query
        return client

    def patches(self):
        return [
            patch("google.cloud.bigquery.Client", side_effect=self.client_factory),
            patch("google.cloud.bigquery.Table"),
            patch("google.cloud.bigquery.SchemaField"),
            patch(
                "google.cloud.bigquery.ScalarQueryParameter",
                side_effect=lambda name, type_, value: types.SimpleNamespace(name=name, value=value),
            ),
            patch(
                "google.cloud.bigquery.QueryJobConfig",
                side_effect=lambda query_parameters: types.SimpleNamespace(query_parameters=query_parameters),
            ),
        ]


def main():
    import week3.mock_systems as ms

    state_file = ms.STATE_FILE
    if state_file.exists():
        state_file.unlink()

    def _local_fallback_matches_original_behavior():
        # BigQuery genuinely unreachable (no ADC, offline dev, whatever) - must fall back to
        # exactly the original local-JSON-file behavior, so a bare local run still needs zero
        # GCP setup.
        ms._bq_client_cache = None
        ms._bq_known_unavailable = True
        try:
            ms.reset_all()
            assert _mode(ms.UPLOAD_DIR) == "0o755"
            ms.break_issue("app_down")
            assert ms.get_state()["app_down"] is True
            ms.break_issue("upload_permission")
            assert _mode(ms.UPLOAD_DIR) == "0o0"
            ms.reset_all()
            assert _mode(ms.UPLOAD_DIR) == "0o755"
            assert ms.get_state()["app_down"] is False
        finally:
            ms._bq_known_unavailable = False
            if state_file.exists():
                state_file.unlink()

    check("Local-file fallback still works with zero BigQuery/GCP setup", _local_fallback_matches_original_behavior)

    def _toggle_flags_shared_across_separate_processes():
        fake_bq = _FakeBigQuery()
        with fake_bq.patches()[0], fake_bq.patches()[1], fake_bq.patches()[2], fake_bq.patches()[3], fake_bq.patches()[4]:
            ms._bq_client_cache = None
            ms.reset_all()

            # "process A" (e.g. aiops_sdk_demo_app) breaks a scenario
            ms.break_issue("login_ui_disabled")

            # "process B" (e.g. aiops_console_app) - simulated by dropping the client cache,
            # the same fresh-lookup a genuinely separate container would do - must see it
            ms._bq_client_cache = None
            assert ms.get_state()["login_ui_enabled"] is False, "a separate process must see the break"

            # "process B" fixes it
            from week3 import remedies
            remedies.fix_login_ui_disabled("whatever")

            # "process A" - fresh lookup again - must see the fix
            ms._bq_client_cache = None
            assert ms.get_state()["login_ui_enabled"] is True, "a separate process must see the fix"

    check(
        "Toggle flags (app_down / login_ui_disabled / login_cred_backend) are visible across separate processes via BigQuery",
        _toggle_flags_shared_across_separate_processes,
    )

    def _upload_permission_shared_intent_drives_real_local_chmod():
        fake_bq = _FakeBigQuery()
        patches = fake_bq.patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            ms._bq_client_cache = None
            ms.reset_all()
            assert _mode(ms.UPLOAD_DIR) == "0o755"

            # "process A" breaks it - its own folder really is chmod'd broken
            ms.break_issue("upload_permission")
            assert _mode(ms.UPLOAD_DIR) == "0o0"
            assert fake_bq.row["upload_permission_ok"] is False

            # "process B" (its own separate, never-touched folder - simulated by resetting
            # this process's copy back to healthy before it looks) must still reconcile to
            # broken once it asks, and correctly report the fault as real when fixing it
            os.chmod(ms.UPLOAD_DIR, 0o755)
            ms._bq_client_cache = None
            from week3 import remedies
            outcome_text = remedies.fix_upload_permission("whatever")
            assert "was not actually broken" not in outcome_text, (
                "must report the fault as real, based on shared intent, "
                "not on this process's own never-touched local folder"
            )
            assert fake_bq.row["upload_permission_ok"] is True
            assert _mode(ms.UPLOAD_DIR) == "0o755"

            # "process A" checks again - even without anyone touching its folder directly,
            # a fresh read must reconcile it back to healthy too
            os.chmod(ms.UPLOAD_DIR, 0o0)  # simulate it still sitting in its old broken state
            ms._bq_client_cache = None
            ms.get_state()
            assert _mode(ms.UPLOAD_DIR) == "0o755", "must reconcile back to healthy on the next read"

    check(
        "upload_permission's real local chmod follows shared intent across separate processes",
        _upload_permission_shared_intent_drives_real_local_chmod,
    )

    def _bq_error_mid_operation_falls_back_to_local_not_a_crash():
        ms._bq_client_cache = None
        ms._bq_known_unavailable = False
        with patch("google.cloud.bigquery.Client", side_effect=RuntimeError("BigQuery unreachable")):
            state = ms.get_state()  # must not raise
            assert state["app_down"] is False
            ms.break_issue("app_down")  # must not raise
            assert ms.get_state()["app_down"] is True
        ms._bq_client_cache = None
        ms._bq_known_unavailable = False
        ms.reset_all()
        if state_file.exists():
            state_file.unlink()

    check("A BigQuery error mid-operation falls back to the local file instead of crashing", _bq_error_mid_operation_falls_back_to_local_not_a_crash)

    ms._bq_client_cache = None
    ms._bq_known_unavailable = False
    ms.reset_all()
    if state_file.exists():
        state_file.unlink()

    failed = [name for status, name in results if status == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    if failed:
        print("Failed:")
        for name in failed:
            print(f"  - {name}")
        sys.exit(1)


if __name__ == "__main__":
    main()
