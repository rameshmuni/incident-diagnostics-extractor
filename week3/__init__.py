"""
Week 3: Event-Driven Auto-Remediation - kept as its own package, deliberately separate from
the Week 2 RAG pipeline files at the top of scripts/, so the two assignments stay easy to
tell apart on disk.

Still deployed as part of the ONE Cloud Run service the top-level app.py runs (see that
file, and demo_app.py's docstring below, for why splitting this into a second deployed
service would cost real complexity for zero demo benefit) - this package boundary is about
code organization, not a second deployment.

Contents:
    mock_systems.py     - the state behind all 4 demo scenarios (2 toggles, 2 real faults)
    demo_app.py         - the real small web app (login + upload pages) those scenarios break
    remedies.py         - one auto-fix function per scenario
    auto_remediation.py - matches an incoming incident to a scenario and runs its remedy,
                          with no human step; this is what app.py calls when the
                          human-in-the-loop toggle is off
    standalone_app.py   - optional: run just demo_app.py's pages locally, no GCP credentials
                          needed, for quickly poking at the broken app without booting the
                          full assistant (python3 -m week3.standalone_app)
    demo_toggle.py       - optional: an interactive menu (python3 -m week3.demo_toggle) to
                          break/fix each of the 4 scenarios by picking a number - no curl,
                          no remembering issue_id strings. For demoing against whatever's
                          running in the SAME process (standalone_app.py locally, or app.py
                          itself) - see its own docstring for the Cloud Run caveat.
"""
