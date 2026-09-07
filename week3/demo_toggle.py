"""
A small interactive CLI for demoing Week 3 by hand: pick a number, break that scenario,
show it to whoever's watching, then fix it (either yourself via remedies.py, or let the
assistant's auto-remediation do it). No curl, no JSON, no remembering issue_id strings.

Only touches LOCAL state - the same mock_systems.py file/folder state.py always has, wherever
this process is running:
  - running this against the demo_app.py Flask dev server / week3.standalone_app on your own
    machine: breaks/fixes what a browser hitting that same process sees, immediately.
  - running this on Cloud Run itself (e.g. `gcloud run services proxy` or a one-off exec
    into the container) would work the same way, but running it from your own laptop while
    the assistant is deployed on Cloud Run will NOT affect the deployed service - Cloud
    Run's disk isn't shared with your laptop. For a deployed demo, break/fix through the
    app's own /api/demo/break, /api/demo/reset endpoints instead (or the buttons already
    built into every /demo/* page - see demo_app.py).

Usage - either of these works:
    cd scripts/  &&  python3 -m week3.demo_toggle
    cd scripts/week3/  &&  python3 demo_toggle.py
"""

try:
    # normal case: run as part of the week3 package (python3 -m week3.demo_toggle)
    from . import mock_systems as ms
    from . import remedies
except ImportError:
    # also allow running this file directly (cd week3/ && python3 demo_toggle.py) - there's
    # no package context for a relative import then, but mock_systems.py and remedies.py are
    # right next to this file, so a plain import finds them just fine either way
    import mock_systems as ms
    import remedies

SCENARIOS = [
    ("app_down", "App Down", remedies.fix_app_down),
    ("login_ui_disabled", "Login UI disabled (Sign In button)", remedies.fix_login_ui_disabled),
    ("login_cred_backend", "Login credential backend broken", remedies.fix_login_cred_backend),
    ("upload_permission", "Upload folder permission (real chmod)", remedies.fix_upload_permission),
]


def _print_state():
    state = ms.get_state()
    print("\nCurrent state:")
    print(f"  1) App Down                      -> {'BROKEN (down)' if state['app_down'] else 'healthy'}")
    print(f"  2) Login UI disabled              -> {'BROKEN (disabled)' if not state['login_ui_enabled'] else 'healthy'}")
    print(f"  3) Login credential backend        -> {'BROKEN' if not state['login_cred_backend_ok'] else 'healthy'}")
    print(f"  4) Upload folder permission        -> {'BROKEN (no access)' if not state['upload_permission_ok'] else 'healthy'}")


def _menu():
    print("\n=== Week 3 Demo Toggle ===")
    _print_state()
    print("\nWhat do you want to do?")
    for i, (_, label, _) in enumerate(SCENARIOS, start=1):
        print(f"  break {i}   - break: {label}")
    for i, (_, label, _) in enumerate(SCENARIOS, start=1):
        print(f"  fix   {i}   - run the real remedy for: {label}")
    print("  show        - just show current state again")
    print("  reset       - put everything back to healthy")
    print("  quit        - exit")


def main():
    while True:
        _menu()
        try:
            choice = input("\n> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if choice in ("quit", "q", "exit"):
            break
        elif choice == "show":
            continue  # menu reprints state every loop anyway
        elif choice == "reset":
            ms.reset_all()
            print("Everything reset to healthy.")
        elif choice.startswith("break "):
            _act(choice[len("break "):], mode="break")
        elif choice.startswith("fix "):
            _act(choice[len("fix "):], mode="fix")
        else:
            print(f"Didn't understand {choice!r} - try e.g. 'break 1', 'fix 1', 'reset', 'quit'.")


def _act(num_str, mode):
    try:
        idx = int(num_str) - 1
        issue_id, label, remedy_fn = SCENARIOS[idx]
    except (ValueError, IndexError):
        print(f"Not a valid scenario number: {num_str!r} - pick 1-{len(SCENARIOS)}.")
        return

    if mode == "break":
        ms.break_issue(issue_id)
        print(f"Broke: {label}. Go try it in the browser now.")
    else:
        result_text = remedy_fn(f"demo: fixing {label}")
        print(f"Ran the remedy for: {label}\n")
        print(result_text)


if __name__ == "__main__":
    main()
