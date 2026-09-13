"""
Week 3's actual "auto-fix" scripts - one function per known, small, easy-to-automate issue.

Each function reads the current mock system state (mock_systems.py), applies the fix, saves
the updated state, and returns a plain-language Root Cause + Resolution write-up - the exact
same shape resolve_incident() in seed_resolved_incidents.py already writes into ServiceNow's
close_notes for every resolved incident in the Week 2 corpus. auto_remediation.py is what
decides *which* of these to call for a given incident; these functions just do the fixing.

Every function is idempotent on purpose: calling one when the underlying system is already
healthy doesn't error or "double-fix" anything, it just reports that nothing was wrong. That
matters here because the matcher in auto_remediation.py can only tell that an incident sounds
like a known issue - it can't independently verify the system is actually in that broken state
before deciding to run the remedy.
"""

import mock_systems as ms


def fix_login_failure(incident_text):
    state = ms.get_state()
    login = state["login"]
    was_locked = login["locked"]
    login["locked"] = False
    login["failed_attempts"] = 0
    ms.save_state(state)

    if was_locked:
        return (
            f"Root Cause:\n"
            f"Account '{login['account']}' was locked out after repeated failed login attempts, "
            f"blocking the application from authenticating.\n\n"
            f"Resolution:\n"
            f"Ran the automated account-unlock remedy: cleared the lock flag and reset the "
            f"failed-attempt counter to 0 for '{login['account']}'. Login has been verified as restored."
        )
    return (
        f"Root Cause:\n"
        f"Account '{login['account']}' was not actually locked at the time this remedy ran.\n\n"
        f"Resolution:\n"
        f"No unlock action was necessary - confirmed the account is already in a healthy state."
    )


def fix_stuck_queue(incident_text):
    state = ms.get_state()
    queue = state["queue"]
    was_stuck = queue["status"] == "stuck"
    stuck_item = queue.get("stuck_item_id")
    queue["status"] = "healthy"
    queue["stuck_item_id"] = None
    ms.save_state(state)

    if was_stuck:
        return (
            f"Root Cause:\n"
            f"Queue item '{stuck_item}' in the '{queue['queue_name']}' queue was stuck In Progress, "
            f"blocking every item behind it from being picked up.\n\n"
            f"Resolution:\n"
            f"Ran the automated queue-clear remedy: reset the stuck item's status and released the "
            f"queue so processing resumed. The '{queue['queue_name']}' queue is healthy again."
        )
    return (
        f"Root Cause:\n"
        f"The '{queue['queue_name']}' queue was not actually stuck at the time this remedy ran.\n\n"
        f"Resolution:\n"
        f"No queue action was necessary - confirmed the queue is already processing normally."
    )


def fix_expired_credential(incident_text):
    state = ms.get_state()
    cred = state["credential"]
    was_expired = cred["expired"]
    cred["expired"] = False
    cred["expires_at"] = None
    ms.save_state(state)

    if was_expired:
        return (
            f"Root Cause:\n"
            f"The '{cred['name']}' credential/token had expired, causing authentication failures "
            f"against the downstream system that relies on it.\n\n"
            f"Resolution:\n"
            f"Ran the automated credential-renewal remedy: issued a fresh token for '{cred['name']}' "
            f"and updated the stored value. Authentication has been verified as restored."
        )
    return (
        f"Root Cause:\n"
        f"The '{cred['name']}' credential was not actually expired at the time this remedy ran.\n\n"
        f"Resolution:\n"
        f"No renewal was necessary - confirmed the credential is already valid."
    )


def fix_disk_full(incident_text):
    state = ms.get_state()
    disk = state["disk"]
    was_full = disk["used_percent"] >= 90 or not disk["logs_rotated"]
    before_pct = disk["used_percent"]
    disk["used_percent"] = 40
    disk["logs_rotated"] = True
    ms.save_state(state)

    if was_full:
        return (
            f"Root Cause:\n"
            f"Host '{disk['host']}' was at {before_pct}% disk usage with log rotation not running, "
            f"causing failures writing logs and temporary files.\n\n"
            f"Resolution:\n"
            f"Ran the automated disk-cleanup remedy: rotated and archived old logs and cleared "
            f"temporary files on '{disk['host']}', bringing usage down to a healthy level."
        )
    return (
        f"Root Cause:\n"
        f"Host '{disk['host']}' was not actually low on disk space at the time this remedy ran "
        f"(usage was {before_pct}%).\n\n"
        f"Resolution:\n"
        f"No cleanup was necessary - confirmed disk usage is already within a healthy range."
    )
