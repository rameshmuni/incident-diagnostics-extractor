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

### Remediation, take two: split across two applications

`propose_remedy()`/`run_confirmed_remedy()` above assume the person
proposing and the person approving are the same person, in the same app, in
the same request/response cycle. That's not always the right assumption -
the person who noticed a symptom isn't necessarily who should be trusted to
run a script against production. Three more methods answer that version of
the question, by making a real ServiceNow incident the hand-off between two
separate applications instead of two calls in one process:

```python
# Called from an end-user-facing app (aiops_sdk_demo_app). Matches, and if
# confident, opens a real, unresolved incident tagged for AIOps review.
# Never runs a fix. If nothing matches confidently, escalates instead.
result = sdk.raise_remedy_candidate("Upload fails with a permission error")
# {"decision": "raised_for_aiops_review", "issue_id": "upload_permission",
#  "similarity": 0.81, "incident_number": "INC0012345", "sys_id": "..."}

# Called from a separate, internal app (aiops_console_app). Asks ServiceNow
# directly for every incident raise_remedy_candidate() has opened.
candidates = sdk.list_remedy_candidates()
# [{"number": "INC0012345", "sys_id": "...", "issue_id": "upload_permission",
#   "similarity": 0.81, "incident_text": "...",
#   "remedy_explanation": "Runs chmod 755 on the uploads folder..."}]

# Called from that same internal app, only after someone there clicks
# Approve. Runs the real fix and resolves the SAME incident by sys_id.
outcome = sdk.run_confirmed_remedy_for_candidate(
    candidates[0]["sys_id"], candidates[0]["incident_text"],
    candidates[0]["issue_id"], approved_by="priya (aiops)",
)
```

See `aiops_sdk_demo_app/README.md` and `aiops_console_app/README.md` for
the two apps that actually use this split, and `remediation.py`'s module
docstring for why all three remediation flows are kept side by side rather
than one replacing the others.

### Keeping the retrieval corpus current: `refresh_corpus()`

Resolving an incident (via any of the flows above) only ever touches
ServiceNow. Week 2's "similar past incidents" search reads from a separate
BigQuery table that's built by two other scripts
(`fetch_resolved_incidents.py`, `build_bigquery_corpus.py`) - originally a
manual step, run by hand whenever someone remembered to. `IncidentSearch`
now wraps that pair as one method:

```python
new_count = sdk.search.refresh_corpus()  # chains both scripts, returns the new corpus_size()
```

`aiops_console_app` calls this automatically, in a background thread, right
after every approved fix - see that app's README for why it's backgrounded
rather than inline. Nothing about `fetch_resolved_incidents.py` or
`build_bigquery_corpus.py` was changed to add this - `refresh_corpus()`
reuses `to_corpus_records()` and `main()` from those two files unmodified,
and only replaces the ServiceNow query that decides which incidents are "in
scope."

That one difference matters: `fetch_resolved_incidents.py`'s own query only
asks for `state=6` (Resolved). This project already learned, while fixing
the "resolved candidates keep reappearing" bug, that on this instance
`active` only goes false once an incident reaches Closed (state 7) -
Resolved alone doesn't do it. That's a strong sign incidents here really do
age from Resolved into Closed on their own over time - and once one does,
a `state=6`-only query stops seeing it, even though its close_notes are
just as valid a past RCA as they ever were. `refresh_corpus()` asks for
`stateIN6,7` instead, specifically so the corpus (and the count in
`aiops_sdk_demo_app`'s status pill) can only grow as things get fixed, never
quietly shrink because older resolved incidents finished their lifecycle.

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

## `app.py` still was not touched

Every example above is adopted somewhere - `aiops_sdk_demo_app` uses
`suggest()`/`raise_remedy_candidate()`, `aiops_console_app` uses
`list_remedy_candidates()`/`run_confirmed_remedy_for_candidate()` - but
always in one of the two duplicate apps this project added, never in the
original `app.py`. That file, and everything else outside `aiops_sdk/`,
`aiops_sdk_demo_app/`, and `aiops_console_app/`, remains exactly as it was
before any of this work started.
