import os, requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
from collections import Counter
load_dotenv(".env")
resp = requests.get(
    f"https://{os.environ['SN_INSTANCE']}.service-now.com/api/now/table/incident",
    auth=HTTPBasicAuth(os.environ["SN_USER"], os.environ["SN_PASS"]),
    headers={"Accept": "application/json"},
    params={"sysparm_query": "stateIN6,7", "sysparm_fields": "sys_id,number,state,close_notes", "sysparm_limit": 500},
)
rows = resp.json()["result"]
print("total incidents currently Resolved(6)/Closed(7):", len(rows))
print("by state:", dict(Counter(r["state"] for r in rows)))
print("missing close_notes (would be skipped from the corpus):", sum(1 for r in rows if not r.get("close_notes")))