# aiops_sdk

The reusable core behind the AIOps Virtual Agent, packaged as one small
library instead of a handful of scripts imported by file name.

This folder was added new. **Nothing outside `aiops_sdk/` was modified to
build it** - `app.py`, `week3/`, `query_incident.py`,
`query_incident_gemini.py`, and `seed_resolved_incidents.py` are all exactly
as they were. Every class in here is a thin wrapper that imports and calls
those existing, unmodified functions - it does not reimplement retrieval,
matching, remedies, or ServiceNow logic.

## Why this exists

See the "SDK Fundamentals" write-up for the full reasoning. Short version:
today, reusing "search for a similar incident" or "auto-fix a known issue"
anywhere else means copying files out of this project's folder. This package
gives that logic a stable, documented interface instead.

## What's in here

| File | Wraps | Purpose |
|---|---|---|
| `llm.py` | `query_incident_gemini.call_gemini()` | Pluggable model layer - `LLMProvider` is the contract, `GeminiProvider` is today's implementation |
| `retrieval.py` | `query_incident.py`'s embed/search/build_prompt/corpus_size | `IncidentSearch` - similar-past-incident lookup |
| `servicenow.py` | `seed_resolved_incidents.py` + `query_incident.escalate_to_developer` | `ServiceNowClient` - create / resolve / escalate |
| `remediation.py` | `week3/auto_remediation.py` | `Remediator` - match a known issue, run the real fix, resolve or escalate |
| `client.py` | all of the above | `AiopsSDK` - the one object a consumer actually creates |

## Usage

```python
from aiops_sdk import AiopsSDK

sdk = AiopsSDK()  # defaults to GeminiProvider - same call_gemini() app.py already uses

# Week 2 flow: retrieve similar incidents + ask the LLM which one applies.
# The caller still owns the human-verdict step - this never writes to ServiceNow.
result = sdk.suggest("Users can log in but the upload page won't accept files")
print(result["matches"])
print(result["suggestion"])
```

### Remediation: every fix requires human approval, no exceptions

Per current direction, a remedy script must never run without a human
approving it first - regardless of how confident the match is. That's a
hard gate, not a confidence threshold, so it's built as two separate calls:

```python
# Step 1 - matches only. Never runs a fix, never touches ServiceNow.
proposal = sdk.propose_remedy("Users can log in but the upload page won't accept files")
print(proposal)
# {"decision": "needs_human_approval", "issue_id": "upload_permission",
#  "matched_description": "...", "similarity": 0.81}
# or {"decision": "no_match", "best_similarity": 0.31, "message": "..."}

# --- show `proposal` to a person; only proceed if they approve it ---

# Step 2 - only ever called after that approval. This is the only method
# in the SDK that actually executes a remedy.
outcome = sdk.run_confirmed_remedy(
    "Users can log in but the upload page won't accept files",
    issue_id=proposal["issue_id"],
    approved_by="ramesh",   # optional, recorded in the ServiceNow ticket for the audit trail
)
print(outcome["outcome"])          # "resolved_with_approval"
print(outcome["incident_number"])  # the ServiceNow ticket that got created + resolved
```

`propose_remedy()` is verified (see `examples/verify_sdk.py`) to never call
a remedy function under any circumstances, including a high-confidence
match - the only way a fix runs is a second, explicit call to
`run_confirmed_remedy()`.

`sdk.auto_remediate(text)` still exists and is still the original,
fully-automatic flow (match → fix → resolve, no human, ever) - unmodified,
matching exactly what's deployed today. It's kept for comparison, not as
the recommended path, now that every fix requires approval.

Swapping the model later means writing a new `LLMProvider` subclass and
passing it in - nothing else changes:

```python
from aiops_sdk import AiopsSDK, LLMProvider

class OpenAIProvider(LLMProvider):
    def generate(self, prompt: str) -> str:
        return call_openai(prompt)  # your own implementation

sdk = AiopsSDK(llm_provider=OpenAIProvider())
```

## Requirements

Constructing `AiopsSDK()` (or any of `IncidentSearch`, `Remediator`,
`ServiceNowClient` on their own) needs the same environment this project
already needs to run - `GEMINI_API_KEY`, `SN_INSTANCE`/`SN_USER`/`SN_PASS`,
and BigQuery/ServiceNow reachability - because it imports the real,
credentialed modules underneath. Nothing about those requirements changed;
the SDK doesn't add new ones or remove the existing ones.

## What this is *not*, yet

This is a working, importable Python package (`import aiops_sdk` works from
inside `scripts/`, exactly like `import week3` already does) - but it is not
yet a standalone `pip install`-able distribution, because it still imports
sibling files (`query_incident.py`, `seed_resolved_incidents.py`, etc.) that
live one level up, on the assumption that `scripts/` is the working
directory - the same assumption `app.py` and `week3/` already make.

Making it a true standalone package would mean either vendoring those
modules inside `aiops_sdk/` or turning the whole `scripts/` folder into its
own installable package with `aiops_sdk` as a dependency - both are real
changes to files outside this folder, so neither was done here on purpose.
That's the natural next increment, not this one.

## Adopting this in `app.py` (not done - for reference only)

`app.py` was not touched. Today, `/api/query` with human-in-the-loop off
calls `attempt_auto_remediation(text)` directly and returns a finished
outcome in one request - no approval step, matching `sdk.auto_remediate()`
above. Adopting the now-required approval gate would mean `app.py` growing
one new endpoint (mirroring the existing `/api/verdict` pattern Week 2
already uses for its own human step), not just swapping an import:

```python
# a new route, e.g. /api/remedy/propose - calls sdk.propose_remedy(text),
# returns the proposal to the UI, does NOT run anything yet

# a new route, e.g. /api/remedy/confirm - calls
# sdk.run_confirmed_remedy(text, issue_id, approved_by=current_user),
# only once the UI has shown the proposal and a person clicked approve
```

That's a real (if small) change to `app.py` and the frontend, so it wasn't
made here - this SDK just has both methods ready for when it is.
