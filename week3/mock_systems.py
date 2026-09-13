"""
The "system state" behind Week 3's 4 demo scenarios - two purposeful toggles (to show
event-based triggers, per the manager's ask) and two genuine technical faults (to show a
real self-heal, not a fake one).

Toggles (fake-by-design, just a flag in state):
    app_down              - the whole demo app is unreachable, as if it crashed / was taken down
    login_ui_enabled      - the login page's Sign In button is disabled in the UI

Real faults (an actual condition on the running container, not just a flag):
    login_cred_backend_ok - False means the login page can't even fetch/verify credentials
                             against its backend config, regardless of whether the password
                             typed in is correct. Modeled as a broken backend config value
                             (login_backend_url() below), so login checks genuinely have
                             nowhere to verify against, rather than a hardcoded "always
                             reject" branch.
    upload folder permission - a REAL os.chmod() on the uploads/ folder on disk. When broken,
                             saving a file into that folder gets a genuine PermissionError,
                             the same way it would if an ops team really misconfigured a
                             folder's permissions. This only works because the container
                             runs as a non-root user (see Dockerfile) - root ignores
                             permission bits entirely, which would make this fault fake too.

Shared state, not per-container state: this project now runs as more than one process at
once (the original app, aiops_sdk_demo_app, aiops_console_app - each its own Cloud Run
service with its own private, ephemeral disk). A local JSON file used to be the whole story
here, which worked when everything ran as one process on one machine sharing one disk - but
once aiops_console_app runs a remedy, that has to be visible to whichever separate service is
actually rendering the login/upload demo pages, or "approve the fix" would silently do
nothing observable. So the 4 flags below are read from / written to one shared BigQuery table
(same dataset Week 2's corpus already lives in - no new GCP service to enable, just one more
tiny table) - every process asks BigQuery for the current truth instead of trusting its own
disk. If BigQuery isn't reachable at all (no ADC configured, fully offline local dev), this
transparently falls back to the original local JSON file behavior - a bare `python3
mock_systems.py break app_down` still works with zero GCP setup, it just won't be visible to
a different process/container the way the BigQuery-backed path is.

The real upload-folder permission fault is the one exception that can't just be "a shared
flag" - a chmod is inherently local to whichever container's disk actually has that folder.
So upload_permission_ok is still tracked as shared *intent* ("should uploads be broken right
now") in that same BigQuery row, but each container reconciles its own real folder permission
to match that shared intent every time anything asks about it (get_state(), upload_dir_writable())
- cheap enough to do on every call, and it's what makes "break it from one app, watch a
different app's upload page genuinely reject writes" actually true.

Usage during a demo (run from inside week3/, or as `python3 -m week3.mock_systems ...`
from scripts/):
    python3 mock_systems.py break app_down
    python3 mock_systems.py break login_ui_disabled
    python3 mock_systems.py break login_cred_backend
    python3 mock_systems.py break upload_permission
    python3 mock_systems.py show                      # print current state of everything
    python3 mock_systems.py reset                     # put everything back to healthy
"""

import json
import os
import sys
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent / "mock_systems_state.json"

# same dataset Week 2's corpus table lives in (query_incident.py's BQ_DATASET) - not imported
# from there on purpose, so this module never picks up query_incident.py's other dependencies
# (Gemini, ServiceNow env vars) just to toggle a demo flag. One more small table in that same
# dataset, not a new GCP service to enable.
BQ_DATASET = os.environ.get("BQ_DATASET", "incident_assistant")
DEMO_STATE_TABLE = "demo_state"
DEMO_STATE_ROW_ID = "singleton"  # this table only ever has exactly one row

# the real folder demo_app.py's /demo/upload route actually saves uploaded files into.
# Created fresh by the Dockerfile at build time (owned by the non-root appuser);
# ensure_upload_dir() below recreates it the same way if it's ever missing locally.
UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"
UPLOAD_DIR_HEALTHY_MODE = 0o755  # owner rwx, group/other rx - normal, writable-by-owner folder
UPLOAD_DIR_BROKEN_MODE = 0o000  # nobody - not even the owning process - can read or write it

# a real (if toy) "backend config" the login page depends on to verify credentials against -
# breaking login_cred_backend means this points somewhere that can't actually be reached,
# which is what makes that scenario a config fault rather than a hardcoded failure branch
LOGIN_BACKEND_URL_HEALTHY = "https://idp.internal.demo/verify"
LOGIN_BACKEND_URL_BROKEN = ""  # blanked out, as if someone wiped the config value

# the one account the demo login page actually checks submitted credentials against
DEMO_USERS = {"demo_user": "Summer#2026"}

# healthy defaults for the 4 flags this file persists (to BigQuery, or the local file as a
# fallback - see module docstring). upload_permission_ok is included here now too: it's still
# reconciled against the real folder on every read (see get_state()/upload_dir_writable()),
# but the *intent* has to be shared state like the other 3, or a remedy run from a different
# container than the one serving /demo/upload would have nothing to tell that container to
# fix its own folder.
#
# Deliberately has no notion of a ServiceNow incident anywhere in this module - this file
# only ever tracks the demo app's own state. Incidents are created and resolved exclusively
# through the chat's attempt_auto_remediation() (see auto_remediation.py) and, in the
# split-audience flow, aiops_sdk/remediation.py - never from here.
DEFAULT_STATE = {
    "app_down": False,
    "login_ui_enabled": True,
    "login_cred_backend_ok": True,
    "upload_permission_ok": True,
}

# same 4 issue_ids auto_remediation.py's CATALOG uses - kept as one tuple so break_issue()
# and the CLI/validation below can't silently drift out of sync with the catalog
ISSUE_IDS = ("app_down", "login_ui_disabled", "login_cred_backend", "upload_permission")

_bq_client_cache = None
_bq_known_unavailable = False  # sticky per-process: once BigQuery proves unreachable, stop
                                # retrying it on every single call and just use the local file
                                # for the rest of this process's life


def _get_bq_client():
    """Lazy + cached, same pattern query_incident.py's get_bq_client() uses - not imported
    from there directly (see the BQ_DATASET comment above for why). Returns None (never
    raises) if BigQuery isn't usable right now, so every caller below has one simple
    "did we get a client or not" branch instead of scattered try/excepts."""
    global _bq_client_cache, _bq_known_unavailable
    if _bq_known_unavailable:
        return None
    if _bq_client_cache is None:
        try:
            from google.cloud import bigquery
            _bq_client_cache = bigquery.Client()
        except Exception:
            _bq_known_unavailable = True
            return None
    return _bq_client_cache


def _demo_state_table_id(client):
    return f"{client.project}.{BQ_DATASET}.{DEMO_STATE_TABLE}"


def _ensure_demo_state_table(client, table_id):
    from google.cloud import bigquery

    schema = [
        bigquery.SchemaField("id", "STRING"),
        bigquery.SchemaField("app_down", "BOOL"),
        bigquery.SchemaField("login_ui_enabled", "BOOL"),
        bigquery.SchemaField("login_cred_backend_ok", "BOOL"),
        bigquery.SchemaField("upload_permission_ok", "BOOL"),
    ]
    client.create_table(bigquery.Table(table_id, schema=schema), exists_ok=True)


def _load_bq():
    """None if BigQuery isn't reachable at all; otherwise the current shared state, creating
    the table/row with DEFAULT_STATE the first time this ever runs against a fresh project."""
    client = _get_bq_client()
    if client is None:
        return None
    try:
        table_id = _demo_state_table_id(client)
        _ensure_demo_state_table(client, table_id)
        rows = list(client.query(
            f"SELECT * FROM `{table_id}` WHERE id = @id LIMIT 1",
            job_config=_bq_param_config({"id": ("STRING", DEMO_STATE_ROW_ID)}),
        ).result())
        if rows:
            row = rows[0]
            return {k: bool(row[k]) for k in DEFAULT_STATE}
        _save_bq(client, dict(DEFAULT_STATE))
        return dict(DEFAULT_STATE)
    except Exception:
        return None


def _save_bq(client, state):
    """MERGE instead of DELETE+INSERT so this is one atomic statement, not two - this table
    only ever has the one row (id='singleton'), updated in place."""
    from google.cloud import bigquery

    table_id = _demo_state_table_id(client)
    _ensure_demo_state_table(client, table_id)
    flags = {k: bool(state.get(k, v)) for k, v in DEFAULT_STATE.items()}
    params = {"id": ("STRING", DEMO_STATE_ROW_ID), **{k: ("BOOL", v) for k, v in flags.items()}}
    set_clause = ", ".join(f"{k} = @{k}" for k in flags)
    insert_cols = ", ".join(["id"] + list(flags))
    insert_vals = ", ".join(["@id"] + [f"@{k}" for k in flags])
    client.query(
        f"MERGE `{table_id}` T USING (SELECT @id AS id) S ON T.id = S.id "
        f"WHEN MATCHED THEN UPDATE SET {set_clause} "
        f"WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})",
        job_config=_bq_param_config(params),
    ).result()


def _bq_param_config(params):
    from google.cloud import bigquery

    return bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter(name, type_, value) for name, (type_, value) in params.items()
    ])


def _load_local():
    if not STATE_FILE.exists():
        _save_local(dict(DEFAULT_STATE))
        return dict(DEFAULT_STATE)
    return json.loads(STATE_FILE.read_text())


def _save_local(state):
    flags = {k: bool(state.get(k, v)) for k, v in DEFAULT_STATE.items()}
    STATE_FILE.write_text(json.dumps(flags, indent=2))


def _load():
    state = _load_bq()
    return state if state is not None else _load_local()


def _save(state):
    client = _get_bq_client()
    if client is not None:
        try:
            _save_bq(client, state)
            return
        except Exception:
            pass
    _save_local(state)


def ensure_upload_dir():
    """Create the real uploads/ folder in its healthy (writable) mode if it doesn't exist yet.

    Only touches a folder it just created - an existing one is left exactly as-is here;
    _reconcile_upload_dir() below is what actually applies the current shared intent to it.
    """
    if not UPLOAD_DIR.exists():
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(UPLOAD_DIR, UPLOAD_DIR_HEALTHY_MODE)


def _reconcile_upload_dir(should_be_ok):
    """Makes THIS container's real upload folder permission match the shared intent. Called
    on every state read (get_state(), upload_dir_writable()) so a container that never ran
    the break/fix call itself - because that happened in a different Cloud Run service -
    still ends up with the physically correct real permission before anyone touches uploads.
    Cheap (one os.chmod), safe to call unconditionally every time."""
    ensure_upload_dir()
    os.chmod(UPLOAD_DIR, UPLOAD_DIR_HEALTHY_MODE if should_be_ok else UPLOAD_DIR_BROKEN_MODE)


def upload_dir_writable():
    """Live check of the real folder, not a cached flag - this IS the fault, not a report of
    it. Reconciles against the shared intent first, so this is accurate immediately after a
    break/fix that happened on a different container."""
    state = _load()
    _reconcile_upload_dir(state.get("upload_permission_ok", True))
    return os.access(UPLOAD_DIR, os.W_OK)


def break_upload_permission():
    """The one real (non-flag) fault: an actual chmod that denies writes, including to the
    owning process - Linux still enforces permission bits against a non-root owner; it only
    stops enforcing them for root (see the Dockerfile's non-root USER for why that matters).
    Also persists the shared intent, so a different container serving /demo/upload picks
    this up the next time anyone asks."""
    state = _load()
    state["upload_permission_ok"] = False
    _save(state)
    _reconcile_upload_dir(False)


def restore_upload_permission():
    state = _load()
    state["upload_permission_ok"] = True
    _save(state)
    _reconcile_upload_dir(True)


def get_state():
    """Current state of everything, as a plain dict ready for the /api/demo/state UI.

    The 3 toggle flags come from shared state (BigQuery, or the local file as a fallback -
    see module docstring). upload_permission_ok is reconciled against the real folder on
    disk every time and the live result is what's returned, since that one's a genuine
    fault, not a flag - but the shared intent is what drives which way it gets reconciled.
    """
    state = _load()
    _reconcile_upload_dir(state.get("upload_permission_ok", True))
    state["upload_permission_ok"] = os.access(UPLOAD_DIR, os.W_OK)
    return state


def save_state(state):
    """Persist the toggle flags - remedies.py calls this after fixing app_down /
    login_ui_disabled / login_cred_backend. (upload_permission_ok is also included since
    get_state() always hands back a current, correct value for it - re-persisting it here
    is a harmless no-op, not a separate write path.)"""
    _save({k: state[k] for k in DEFAULT_STATE if k in state})


def break_issue(issue_id):
    """Force one of the 4 scenarios into its broken state, ready for a demo.
    Leaves the other 3 untouched."""
    if issue_id not in ISSUE_IDS:
        raise ValueError(f"unknown issue_id {issue_id!r} - expected one of {list(ISSUE_IDS)}")

    if issue_id == "upload_permission":
        break_upload_permission()
        return get_state()

    state = _load()
    if issue_id == "app_down":
        state["app_down"] = True
    elif issue_id == "login_ui_disabled":
        state["login_ui_enabled"] = False
    elif issue_id == "login_cred_backend":
        state["login_cred_backend_ok"] = False
    _save(state)
    return get_state()


def login_backend_url():
    """What the login page's credential check treats as its backend config value right now -
    a real (if toy) example of "the app can't fetch/verify creds due to a config issue"."""
    return LOGIN_BACKEND_URL_HEALTHY if _load().get("login_cred_backend_ok", True) else LOGIN_BACKEND_URL_BROKEN


def reset_all():
    """Put every scenario back to healthy - all 3 flags AND the real upload folder permission."""
    _save(dict(DEFAULT_STATE))
    _reconcile_upload_dir(True)
    return get_state()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]
    if cmd == "break" and len(args) == 2:
        new_state = break_issue(args[1])
        print(f"Broke {args[1]!r}. Current state:")
        print(json.dumps(new_state, indent=2))
    elif cmd == "show":
        print(json.dumps(get_state(), indent=2))
    elif cmd == "reset":
        print(json.dumps(reset_all(), indent=2))
    else:
        print(__doc__)
        sys.exit(1)
