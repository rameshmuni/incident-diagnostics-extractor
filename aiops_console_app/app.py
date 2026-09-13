"""
The AIOps team's own application - a third app, separate from both the
original app.py and aiops_sdk_demo_app/app.py, built for a different
audience entirely. Where the other two apps let someone describe an
incident, this one lets someone with operational authority review what's
waiting for a decision and, if it's safe, approve it.

Why this exists: aiops_sdk_demo_app's chat can recognize a known issue and
open a ServiceNow incident for it (sdk.raise_remedy_candidate()), but it has
no button and no code path that can run the actual fix - see that app's
module docstring. Something still has to look at that incident, understand
what the remedy would do, and decide whether to run it. This app is that
something. It is the ONLY place in this entire project that ever calls
sdk.run_confirmed_remedy_for_candidate() - the one method that actually
executes a remedy script and resolves an incident.

Nothing about the original project, or about aiops_sdk_demo_app, changed to
build this. This is a third new folder, importing the same aiops_sdk
package the other duplicate already uses - it introduces zero new business
logic of its own; every route below is a thin call into
aiops_sdk/remediation.py's split-audience methods (see that file's module
docstring for the full flow).

The hand-off between this app and aiops_sdk_demo_app is a real ServiceNow
incident, not a shared Python process or a shared file - open two browser
tabs, one on each app, and you're looking at two independent front doors
onto the same ServiceNow instance.

Run it (from the repo root, or from anywhere - same sys.path trick the
other two apps use):
    python3 aiops_console_app/app.py
Defaults to port 5003 - the original app.py uses 5001, aiops_sdk_demo_app
uses 5002.
"""

import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, request, send_from_directory

from aiops_sdk import AiopsSDK

app = Flask(__name__, static_folder="static", static_url_path="")

sdk = AiopsSDK()

# In-memory status for the background corpus refresh kicked off after each
# approved fix - see _start_corpus_refresh() below. Deliberately just a dict
# behind a lock, not a job queue or a DB row: this app has exactly one
# process and the console is the only thing that ever reads it, so anything
# heavier would be solving a problem this prototype doesn't have.
_refresh_lock = threading.Lock()
_refresh_state = {
    "status": "idle",       # idle | running | done | error
    "corpus_size": None,    # last known count, once a refresh has completed at least once
    "started_at": None,
    "finished_at": None,
    "error": None,
}


def _run_corpus_refresh():
    # Runs on a background thread so approving a fix returns to the browser
    # immediately - refresh_corpus() re-embeds the whole resolved-incident
    # corpus (see its docstring) and can easily take a minute or more.
    with _refresh_lock:
        _refresh_state.update(status="running", started_at=time.time(), finished_at=None, error=None)
    try:
        size = sdk.search.refresh_corpus()
        with _refresh_lock:
            _refresh_state.update(status="done", corpus_size=size, finished_at=time.time())
    except Exception as exc:  # noqa: BLE001 - a failed refresh must never look like the fix itself failed
        with _refresh_lock:
            _refresh_state.update(status="error", error=str(exc), finished_at=time.time())


def _start_corpus_refresh():
    # Only ever one refresh in flight at a time - if the console fires a
    # second approval while one is still running, that one's own resolved
    # incident will simply be picked up by the refresh already in progress
    # (fetch_resolved_incidents.py reads everything resolved in ServiceNow,
    # not just what triggered this call) or the next one a caller starts.
    with _refresh_lock:
        if _refresh_state["status"] == "running":
            return "already_running"
    threading.Thread(target=_run_corpus_refresh, daemon=True).start()
    return "started"


@app.route("/")
def home():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/refresh_status")
def refresh_status():
    # Polled by the console's UI after an approval, so the "the fix ran, is
    # it actually searchable yet" question has a real, live answer instead
    # of requiring someone to know to re-run two scripts by hand.
    with _refresh_lock:
        return jsonify({"ok": True, **_refresh_state})


@app.route("/api/refresh_corpus", methods=["POST"])
def refresh_corpus():
    # Manual trigger, independent of approving a candidate - the only other
    # way this ever starts. Exists for exactly the situation this project
    # just ran into: the corpus-rebuild code changed, but nothing re-runs it
    # until the next approval, so there was no way to test or force a fresh
    # rebuild against whatever's in ServiceNow *right now* without waiting
    # for a new incident to approve. Same underlying _start_corpus_refresh()
    # as the post-approval path - this route doesn't touch ServiceNow or run
    # any remedy, it only (re)builds the retrieval corpus.
    return jsonify({"ok": True, "corpus_refresh": _start_corpus_refresh()})


@app.route("/api/candidates")
def list_candidates():
    # Every open incident waiting for an AIOps decision, fetched live from
    # ServiceNow on every request - there is no cache and no local queue to
    # get out of sync with reality.
    try:
        return jsonify({"ok": True, "candidates": sdk.list_remedy_candidates()})
    except Exception as exc:  # noqa: BLE001 - real error to the UI, not a stack trace
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/candidates/confirm", methods=["POST"])
def confirm_candidate():
    # The one route in this entire project (across all three apps) that
    # calls sdk.run_confirmed_remedy_for_candidate() - the only method
    # anywhere that both runs a real remedy script AND resolves an
    # incident. It only runs because a person on THIS app's page clicked
    # "Approve & run fix" - there is no other caller.
    try:
        data = request.get_json(force=True)
        sys_id = data["sys_id"]
        incident_text = data["incident_text"]
        issue_id = data["issue_id"]
        approved_by = data.get("approved_by") or "aiops-team (via console)"

        outcome = sdk.run_confirmed_remedy_for_candidate(
            sys_id, incident_text, issue_id, approved_by=approved_by,
        )
        # The incident is resolved in ServiceNow at this point - but the
        # BigQuery corpus Week 2's retrieval reads from doesn't know that
        # yet (see aiops_sdk/retrieval.py's refresh_corpus() docstring for
        # why this project never kept those two in sync automatically
        # before now). Kick off a rebuild in the background rather than
        # blocking this response on it - refresh_corpus() can take a
        # minute or more.
        corpus_refresh = _start_corpus_refresh()
        return jsonify({"ok": True, "corpus_refresh": corpus_refresh, **outcome})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5003))
    app.run(host="0.0.0.0", port=port)
