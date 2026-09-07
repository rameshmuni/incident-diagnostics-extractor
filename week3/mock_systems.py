"""
The "system state" behind Week 3's 4 demo scenarios - two purposeful toggles (to show
event-based triggers, per the manager's ask) and two genuine technical faults (to show a
real self-heal, not a fake one).

Toggles (fake-by-design, just a flag in state):
    app_down              - the whole demo app is unreachable, as if it crashed / was taken down
    login_ui_enabled      - the login page's Sign In button is disabled in the UI

Real faults (an actual condition on the running container, not a flag):
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

Cloud Run's local disk isn't shared across instances or with Cloud Shell, so - same as
app.py's /api/demo/* routes already note - "break" and "fix" both have to run as API calls
against the one running service instance for a demo to see consistent before/after state.

Both the state file and the real uploads/ folder this module manages live inside this
week3/ package's own directory (see STATE_FILE and UPLOAD_DIR below) - deliberately, so
every runtime artifact Week 3 produces stays inside week3/ rather than leaking back into
the shared scripts/ folder Week 2's files live in.

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

# healthy defaults for the 3 flags this file persists to disk. Upload-folder permission is
# NOT one of these - it's real filesystem state, not a flag, so get_state() reads it live
# from disk instead of trusting a value that could drift out of sync with reality.
#
# Deliberately has no notion of a ServiceNow incident anywhere in this module - this file
# only ever tracks the demo app's own state. Incidents are created and resolved exclusively
# through the chat's attempt_auto_remediation() (see auto_remediation.py), never from here.
DEFAULT_STATE = {
    "app_down": False,
    "login_ui_enabled": True,
    "login_cred_backend_ok": True,
}

# same 4 issue_ids auto_remediation.py's CATALOG uses - kept as one tuple so break_issue()
# and the CLI/validation below can't silently drift out of sync with the catalog
ISSUE_IDS = ("app_down", "login_ui_disabled", "login_cred_backend", "upload_permission")


def _load():
    if not STATE_FILE.exists():
        _save(dict(DEFAULT_STATE))
        return dict(DEFAULT_STATE)
    return json.loads(STATE_FILE.read_text())


def _save(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def ensure_upload_dir():
    """Create the real uploads/ folder in its healthy (writable) mode if it doesn't exist yet.

    Only touches a folder it just created - an existing one is left exactly as-is, or every
    request would silently undo a demo's "break upload_permission" state. Idempotent and
    cheap enough to call on every request that touches uploads.
    """
    if not UPLOAD_DIR.exists():
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(UPLOAD_DIR, UPLOAD_DIR_HEALTHY_MODE)


def upload_dir_writable():
    """Live check of the real folder, not a cached flag - this IS the fault, not a report of it."""
    ensure_upload_dir()
    return os.access(UPLOAD_DIR, os.W_OK)


def break_upload_permission():
    """The one real (non-flag) fault: an actual chmod that denies writes, including to the
    owning process - Linux still enforces permission bits against a non-root owner; it only
    stops enforcing them for root (see the Dockerfile's non-root USER for why that matters)."""
    ensure_upload_dir()
    os.chmod(UPLOAD_DIR, UPLOAD_DIR_BROKEN_MODE)


def restore_upload_permission():
    ensure_upload_dir()
    os.chmod(UPLOAD_DIR, UPLOAD_DIR_HEALTHY_MODE)


def get_state():
    """Current state of everything, as a plain dict ready for the /api/demo/state UI.

    The 3 toggle flags come from the JSON file; upload_permission_ok is computed live from
    the real folder on disk every time, since that one's a genuine fault, not a flag.
    """
    state = _load()
    state["upload_permission_ok"] = upload_dir_writable()
    return state


def save_state(state):
    """Persist the 3 flag fields only - remedies.py calls this after fixing a toggle.
    (upload_permission_ok is derived, never written back - see get_state().)"""
    flags = {k: state[k] for k in DEFAULT_STATE if k in state}
    _save(flags)


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
    restore_upload_permission()
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
