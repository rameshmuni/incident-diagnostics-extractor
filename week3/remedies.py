"""
Week 3's actual "auto-fix" scripts - one function per known, small, auto-fixable issue in
the real demo app (demo_app.py). Each function reads/changes real state - two are toggles in
mock_systems.py (there to demonstrate event-based triggers, per the manager's ask), and two
touch something genuinely real: a broken backend config value and a real folder permission.

Every function is idempotent on purpose: calling one when the underlying thing is already
healthy doesn't error or "double-fix" anything, it just reports that nothing was wrong. That
matters because the matcher in auto_remediation.py can only tell that an incident sounds like
a known issue - it can't independently verify the demo app is actually in that broken state
before deciding to run the remedy.

Each function returns a plain-language Root Cause + Resolution write-up - the same shape
resolve_incident() in seed_resolved_incidents.py already writes into ServiceNow's close_notes
for every resolved incident in the Week 2 corpus.
"""

try:
    # normal case: imported as part of the week3 package
    from . import mock_systems as ms
except ImportError:
    # also allow this to be imported when a sibling file ran directly instead of via the
    # package - mock_systems.py is right next to this file either way
    import mock_systems as ms


def fix_app_down(incident_text):
    state = ms.get_state()
    was_down = state["app_down"]
    state["app_down"] = False
    ms.save_state(state)

    if was_down:
        return (
            "Root Cause:\n"
            "The demo application was toggled into a down state (simulating an outage/crash) "
            "and was returning a 503 for every request.\n\n"
            "Resolution:\n"
            "Ran the automated recovery remedy: cleared the down flag and verified the "
            "application is now serving requests normally again."
        )
    return (
        "Root Cause:\n"
        "The application was not actually down at the time this remedy ran.\n\n"
        "Resolution:\n"
        "No recovery action was necessary - confirmed the application is already reachable."
    )


def fix_login_ui_disabled(incident_text):
    state = ms.get_state()
    was_disabled = not state["login_ui_enabled"]
    state["login_ui_enabled"] = True
    ms.save_state(state)

    if was_disabled:
        return (
            "Root Cause:\n"
            "A UI configuration flag was disabling the Sign In button on the login page, "
            "preventing users from attempting to log in at all.\n\n"
            "Resolution:\n"
            "Ran the automated UI-restore remedy: re-enabled the Sign In button. The login "
            "page has been verified as usable again."
        )
    return (
        "Root Cause:\n"
        "The Sign In button was not actually disabled at the time this remedy ran.\n\n"
        "Resolution:\n"
        "No UI change was necessary - confirmed the login page is already fully usable."
    )


def fix_login_cred_backend(incident_text):
    state = ms.get_state()
    was_broken = not state["login_cred_backend_ok"]
    state["login_cred_backend_ok"] = True
    ms.save_state(state)

    if was_broken:
        return (
            "Root Cause:\n"
            "The login page's backend credential-verification endpoint was misconfigured "
            "(the stored config value the app uses to reach it had been cleared), so the "
            "app could not fetch or verify credentials at all - unrelated to whether the "
            "password entered was correct.\n\n"
            "Resolution:\n"
            "Ran the automated config-repair remedy: restored the credential-verification "
            "endpoint's config value. Login has been verified as able to reach it again."
        )
    return (
        "Root Cause:\n"
        "The credential-verification backend was not actually misconfigured at the time this "
        "remedy ran.\n\n"
        "Resolution:\n"
        "No config change was necessary - confirmed the login backend is already reachable."
    )


def fix_upload_permission(incident_text):
    was_broken = not ms.upload_dir_writable()
    ms.restore_upload_permission()

    if was_broken:
        return (
            "Root Cause:\n"
            f"The application's upload storage folder ('{ms.UPLOAD_DIR.name}/') had its file "
            "permissions misconfigured, so the application itself was denied write access - "
            "every file upload failed with a permission error, regardless of file size or type.\n\n"
            "Resolution:\n"
            "Ran the automated permission-repair remedy: restored the folder's permissions "
            "(chmod 755) so the application can write to it again. Verified the folder is "
            "now writable."
        )
    return (
        "Root Cause:\n"
        "The upload storage folder's permissions were not actually broken at the time this "
        "remedy ran.\n\n"
        "Resolution:\n"
        "No permission change was necessary - confirmed the folder is already writable."
    )
