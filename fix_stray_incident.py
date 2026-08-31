import requests

from seed_resolved_incidents import BASE_URL, AUTH, HEADERS, _check, get_caller_sys_id, create_incident, resolve_incident

BROKEN_SYS_ID = "ae448d51930703100f8af847dd03d603"  # INC0010063 - resolved via UI with no real RCA text

RECORD = {
    "short_description": "Robot repeatedly faults after each transaction, leaving queue items stuck In Progress",
    "description": (
        "Over the past several runs, robot BOT-OPS-04 has been faulting shortly after completing "
        "each transaction in the 'DocumentValidation' queue. Each time this happens, the "
        "just-processed queue item remains In Progress instead of being marked Successful, and the "
        "same pattern repeats on the next item picked up."
    ),
    "category": "Software",
    "priority": "2",
    "root_cause": (
        "The robot's process was faulting due to an unhandled exception thrown during a "
        "post-processing cleanup step (closing a stale file handle) that ran after the "
        "transaction's core logic had already completed successfully, but before REFramework's "
        "SetTransactionStatus call in the End Process state. Because the fault occurred after the "
        "actual work was done but before the queue status could be recorded, each affected item "
        "was left sitting In Progress rather than being marked complete, and the robot's session "
        "ended before it could cleanly pick up the next item - repeating the same pattern on every run."
    ),
    "resolution": (
        "Wrapped the post-processing cleanup step in its own try/catch inside the End Process state "
        "so a cleanup failure no longer blocks SetTransactionStatus from running, and moved the "
        "status update to fire immediately after the core transaction logic succeeds rather than at "
        "the very end of the state. Also added a startup routine that scans for and resets any queue "
        "item left In Progress by a machine whose last heartbeat is older than 30 minutes, so a "
        "future recurrence self-heals instead of requiring manual reset."
    ),
}


def delete_incident(sys_id):
    # remove the badly-resolved record so it doesn't pollute the corpus alongside the clean one
    resp = _check(requests.delete(f"{BASE_URL}/table/incident/{sys_id}", auth=AUTH, headers=HEADERS, timeout=30))
    return resp.status_code


def main():
    # entry point: delete the broken record, then create and resolve a clean replacement
    delete_incident(BROKEN_SYS_ID)
    print(f"Deleted broken record {BROKEN_SYS_ID}")

    caller_sys_id = get_caller_sys_id()
    incident = create_incident(RECORD, caller_sys_id)
    resolve_incident(incident["sys_id"], RECORD)
    print(f"Created and resolved {incident['number']} with proper Resolution notes")


if __name__ == "__main__":
    main()
