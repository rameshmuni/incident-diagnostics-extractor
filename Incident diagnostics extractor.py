import os
import re
import json
from pathlib import Path
from datetime import datetime, timezone

import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

INSTANCE = os.environ["SN_INSTANCE"]
USER = os.environ["SN_USER"]
PASSWORD = os.environ["SN_PASS"]

BASE_URL = f"https://{INSTANCE}.service-now.com/api/now"
AUTH = HTTPBasicAuth(USER, PASSWORD)
HEADERS = {"Accept": "application/json"}

# patterns tuned against real ServiceNow demo incident attachments
ERROR_TYPE_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_.]*(?:Error|Exception))\b")
TIMESTAMP_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})\b")
FRAME_RE = re.compile(r"^\s*(at\s+\S+|File \"[^\"]+\", line \d+.*)", re.MULTILINE)
SERVICE_RE = re.compile(r"(service|component|module)[:=]\s*([A-Za-z0-9_\-]+)", re.IGNORECASE)


def fetch_unassigned_incidents(limit=10):
    # pull open incidents with no assignee, newest first
    query = "assigned_toISEMPTY^active=true^ORDERBYDESCsys_created_on"
    fields = "sys_id,number,short_description,description,priority,category,opened_at,state"
    resp = requests.get(
        f"{BASE_URL}/table/incident",
        auth=AUTH, headers=HEADERS, timeout=30,
        params={"sysparm_query": query, "sysparm_limit": limit, "sysparm_fields": fields},
    )
    resp.raise_for_status()
    return resp.json()["result"]


def fetch_attachments(incident_sys_id):
    # list what's attached to this incident (metadata only, no file content yet)
    resp = requests.get(
        f"{BASE_URL}/attachment",
        auth=AUTH, headers=HEADERS, timeout=30,
        params={"sysparm_query": f"table_name=incident^table_sys_id={incident_sys_id}"},
    )
    resp.raise_for_status()
    return resp.json()["result"]


def fetch_attachment_text(attachment_sys_id):
    # download one attachment's actual content by its own sys_id
    resp = requests.get(f"{BASE_URL}/attachment/{attachment_sys_id}/file", auth=AUTH, timeout=30)
    resp.raise_for_status()
    return resp.content.decode("utf-8", errors="replace")


def extract_diagnostics(log_text):
    # run the four regexes against the raw log/stack-trace text
    if not log_text:
        return {"error_type": None, "error_message": None, "timestamp": None, "stack_frames": [], "service": None}

    error_match = ERROR_TYPE_RE.search(log_text)
    ts_match = TIMESTAMP_RE.search(log_text)
    svc_match = SERVICE_RE.search(log_text)
    frames = FRAME_RE.findall(log_text)[:10]

    error_message = None
    if error_match:
        for line in log_text.splitlines():
            if error_match.group(1) in line:
                error_message = line.strip()
                break

    return {
        "error_type": error_match.group(1) if error_match else None,
        "error_message": error_message,
        "timestamp": ts_match.group(1) if ts_match else None,
        "stack_frames": frames,
        "service": svc_match.group(2) if svc_match else None,
    }


def build_record(incident):
    # combine incident fields with parsed diagnostics into one flat record
    attachments = fetch_attachments(incident["sys_id"])
    diagnostics = {"error_type": None, "error_message": None, "timestamp": None, "stack_frames": [], "service": None}

    for att in attachments:
        if att.get("file_name", "").lower().endswith((".txt", ".log")):
            diagnostics = extract_diagnostics(fetch_attachment_text(att["sys_id"]))
            break

    return {
        "incident_id": incident["number"],
        "sys_id": incident["sys_id"],
        "short_description": incident.get("short_description"),
        "category": incident.get("category"),
        "priority": incident.get("priority"),
        "opened_at": incident.get("opened_at"),
        "service": diagnostics["service"] or incident.get("category"),
        "error_type": diagnostics["error_type"],
        "error_message": diagnostics["error_message"],
        "timestamp": diagnostics["timestamp"] or incident.get("opened_at"),
        "stack_frames": diagnostics["stack_frames"],
        "attachment_count": len(attachments),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }


def main(limit=10, out_file="unassigned_incidents_structured.json"):
    # entry point: pull incidents, build records, write them out as JSON
    incidents = fetch_unassigned_incidents(limit)
    print(f"Pulled {len(incidents)} unassigned incident(s).")

    records = [build_record(i) for i in incidents]
    for r in records:
        print(f"  {r['incident_id']}: error_type={r['error_type']!r} attachments={r['attachment_count']}")

    Path(out_file).write_text(json.dumps(records, indent=2))
    print(f"\nWrote {len(records)} record(s) to {out_file}")
    return records


if __name__ == "__main__":
    main()