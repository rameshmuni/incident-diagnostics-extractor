"""
A minimal ServiceNow client: create an incident, resolve one, list open or
resolved ones, and escalate (open an unresolved ticket for a developer).

Plain functions, not a class - call them directly:

    import servicenow_client as sn
    sn.create_incident(short_description="...", description="...")

Supports two auth modes, picked by SN_AUTH_MODE in .env:
  - "basic"          Most ServiceNow instances. requests' auth=(user, pass).
  - "oauth_password"  Only if your instance has Basic Auth disabled for the
                      REST API (we hit exactly this on an internal demo
                      instance) - needs an OAuth Application Registry's
                      Client ID/Secret in addition to the username/password.

If you ever swap ServiceNow for a different ticketing system, write a new
file with the same function names (create_incident, resolve_incident,
escalate, list_open, list_resolved_incidents, get_caller_sys_id) and change
the one `import servicenow_client as sn` line wherever it's used.
"""
import requests

import config

BASE_URL = f"https://{config.SN_INSTANCE}.service-now.com/api/now"
TOKEN_URL = f"https://{config.SN_INSTANCE}.service-now.com/oauth_token.do"

_access_token = None  # only used in oauth_password mode


def _fetch_oauth_token():
    resp = requests.post(TOKEN_URL, data={
        "grant_type": "password",
        "client_id": config.SN_CLIENT_ID,
        "client_secret": config.SN_CLIENT_SECRET,
        "username": config.SN_USER,
        "password": config.SN_PASS,
    }, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"ServiceNow OAuth token request failed: {resp.status_code}\n{resp.text[:300]}")
    return resp.json()["access_token"]


def _headers():
    global _access_token
    if config.SN_AUTH_MODE == "oauth_password":
        if _access_token is None:
            _access_token = _fetch_oauth_token()
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_access_token}",
        }
    return {"Accept": "application/json", "Content-Type": "application/json"}


def _auth():
    if config.SN_AUTH_MODE == "basic":
        return (config.SN_USER, config.SN_PASS)
    return None  # oauth_password mode: the token lives in the header instead


def _request(method, url, **kwargs):
    """Every ServiceNow call goes through here, so the basic/oauth branch
    and the "token expired mid-process, refresh once and retry" logic both
    live in exactly one place instead of being copy-pasted into every
    function below."""
    if not config.SN_INSTANCE:
        raise RuntimeError("SN_INSTANCE is not set in .env - ServiceNow isn't configured yet.")
    kwargs["headers"] = _headers()
    kwargs["auth"] = _auth()
    kwargs.setdefault("timeout", 30)

    resp = getattr(requests, method)(url, **kwargs)
    if resp.status_code == 401 and config.SN_AUTH_MODE == "oauth_password":
        global _access_token
        _access_token = _fetch_oauth_token()  # token expired mid-process - refresh once, retry once
        kwargs["headers"] = _headers()
        resp = getattr(requests, method)(url, **kwargs)
    return resp


def _check(resp):
    if not resp.ok:
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text[:300]
        raise RuntimeError(f"{resp.status_code} on {resp.url}\n{detail}")
    return resp


def get_caller_sys_id(username=None):
    username = username or config.SN_USER
    resp = _check(_request("get", f"{BASE_URL}/table/sys_user", params={
        "sysparm_query": f"user_name={username}",
        "sysparm_fields": "sys_id",
        "sysparm_limit": 1,
    }))
    results = resp.json()["result"]
    if not results:
        raise RuntimeError(f"No ServiceNow user found for username '{username}'")
    return results[0]["sys_id"]


def create_incident(short_description, description, category="Software", priority="3", caller_sys_id=None):
    """Opens a new incident. No state is set, so it lands as New - the same
    as any incident a person would have logged by hand."""
    payload = {
        "short_description": short_description[:160],  # ServiceNow truncates long titles anyway
        "description": description,
        "category": category,
        "priority": priority,
        "caller_id": caller_sys_id or get_caller_sys_id(),
    }
    resp = _check(_request("post", f"{BASE_URL}/table/incident", json=payload))
    return resp.json()["result"]


def resolve_incident(sys_id, root_cause, resolution):
    close_notes = f"Root Cause:\n{root_cause}\n\nResolution:\n{resolution}"
    payload = {
        "state": "6",  # Resolved
        "close_code": "Solution provided",
        "close_notes": close_notes,
    }
    resp = _check(_request("patch", f"{BASE_URL}/table/incident/{sys_id}", json=payload))
    return resp.json()["result"]


def escalate(new_incident_text):
    """No confident match anywhere in the knowledge base - open a fresh
    ticket so a human investigates from scratch, same as query_incident.py
    does when a human picks "none of these apply"."""
    return create_incident(
        short_description=new_incident_text,
        description=(
            "Auto-logged by the incident copilot: no past resolved incident or knowledge-base "
            "article was a confident match, so this needs a developer to investigate from "
            "scratch.\n\n"
            f"{new_incident_text}"
        ),
    )


def list_open(description_contains=None, limit=50):
    """Every incident ServiceNow considers still active (not resolved or
    closed), optionally narrowed to ones whose description contains a given
    substring."""
    query_parts = ["active=true", "stateNOT IN6,7"]
    if description_contains:
        query_parts.append(f"descriptionLIKE{description_contains}")
    resp = _check(_request("get", f"{BASE_URL}/table/incident", params={
        "sysparm_query": "^".join(query_parts),
        "sysparm_fields": "sys_id,number,short_description,description,priority,category,state,opened_at",
        "sysparm_limit": limit,
    }))
    return resp.json()["result"]


def list_resolved_incidents(limit=1000):
    """Every incident ServiceNow considers resolved or closed - the raw
    material ingest_servicenow.py turns into knowledge-base entries."""
    resp = _check(_request("get", f"{BASE_URL}/table/incident", params={
        "sysparm_query": "stateIN6,7",
        "sysparm_fields": "sys_id,number,short_description,description,category,close_notes",
        "sysparm_limit": limit,
    }))
    return resp.json()["result"]


if __name__ == "__main__":
    # Quick manual check: `python servicenow_client.py` should print how
    # many resolved incidents it can see.
    incidents = list_resolved_incidents()
    print(f"Found {len(incidents)} resolved/closed incident(s) on {config.SN_INSTANCE}.")
