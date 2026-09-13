"""
A tiny simulated "system state" standing in for the real login system, job queue,
credential store, and disk that a production remedy script would actually touch.

There's no real UiPath Orchestrator / IAM / VDI connected to this project, so the Week 3
remedy scripts in remedies.py need something real to read and change in order to
demonstrably fix something rather than just print a canned message. This file is that
something: a small JSON file, read and rewritten like any other piece of state, that starts
"broken" for whichever issue you're demoing and gets genuinely fixed when the matching
remedy runs.

Usage during a demo:
    python3 mock_systems.py break login_failure       # put the login system into a broken state
    python3 mock_systems.py break stuck_queue
    python3 mock_systems.py break expired_credential
    python3 mock_systems.py break disk_full
    python3 mock_systems.py show                      # print the current state of everything
    python3 mock_systems.py reset                     # put everything back to healthy
"""

import json
import sys
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent / "mock_systems_state.json"

# one entry per Week 3 auto-fixable issue - healthy/default values
DEFAULT_STATE = {
    "login": {"account": "svc_uipath_prod", "locked": False, "failed_attempts": 0},
    "queue": {"queue_name": "DocumentValidation", "stuck_item_id": None, "status": "healthy"},
    "credential": {"name": "ORCH_API_TOKEN", "expired": False, "expires_at": None},
    "disk": {"host": "ROBOT-VDI-04", "used_percent": 40, "logs_rotated": True},
}

# what "broken" looks like for each issue, keyed by the same issue_id used in
# auto_remediation.py's CATALOG - this is what break_issue() switches a system into
BROKEN_STATE = {
    "login_failure": ("login", {"account": "svc_uipath_prod", "locked": True, "failed_attempts": 6}),
    "stuck_queue": ("queue", {"queue_name": "DocumentValidation", "stuck_item_id": "Q-48213", "status": "stuck"}),
    "expired_credential": ("credential", {"name": "ORCH_API_TOKEN", "expired": True, "expires_at": "2026-09-01T00:00:00Z"}),
    "disk_full": ("disk", {"host": "ROBOT-VDI-04", "used_percent": 97, "logs_rotated": False}),
}


def _load():
    if not STATE_FILE.exists():
        _save(DEFAULT_STATE)
        return json.loads(json.dumps(DEFAULT_STATE))  # deep copy
    return json.loads(STATE_FILE.read_text())


def _save(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def get_state():
    """The current state of every mock system, as a plain dict."""
    return _load()


def save_state(state):
    """Persist a modified state dict - remedies.py calls this after fixing something."""
    _save(state)


def break_issue(issue_id):
    """Force one system into its broken state, ready for a demo. Leaves the others untouched."""
    if issue_id not in BROKEN_STATE:
        raise ValueError(f"unknown issue_id {issue_id!r} - expected one of {list(BROKEN_STATE)}")
    state = _load()
    key, broken_value = BROKEN_STATE[issue_id]
    state[key] = broken_value
    _save(state)
    return state


def reset_all():
    """Put every mock system back to its healthy default - useful between demo runs."""
    _save(json.loads(json.dumps(DEFAULT_STATE)))
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
