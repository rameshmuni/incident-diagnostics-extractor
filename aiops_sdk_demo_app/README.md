# aiops_sdk_demo_app

A second, complete copy of the AIOps Virtual Agent web app - same UI, same
behavior on the retrieval side - except its backend is built on `aiops_sdk`
instead of six separate script imports, and it has **no code path capable of
running a remedy script.** Where `app.py` (the original) has:

```python
from query_incident import build_prompt, corpus_size, embed_query, escalate_to_developer, log_feedback, parse_slm_pick, search
from query_incident_gemini import GEMINI_MODEL, call_gemini
from week3.auto_remediation import attempt_auto_remediation
```

this app has:

```python
from aiops_sdk import AiopsSDK
sdk = AiopsSDK()
```

...and every route calls `sdk.something(...)` instead. Retrieval and
suggestion behave identically to the original. Known-issue handling does
not - see below.

## Nothing existing was touched

This is a **new folder**, sitting next to `aiops_sdk/`. `app.py`, `week3/`,
`query_incident.py`, `query_incident_gemini.py`, and `seed_resolved_incidents.py`
are all exactly as they were - this app *imports* them (through `aiops_sdk`,
and directly for the `/demo` login/upload pages - see below), it does not
copy or modify any of them. `git status` after this was added shows only
`aiops_sdk/`, `aiops_sdk_demo_app/`, and `aiops_console_app/` as new.

## How to run it

From the repo root, with the same `.env` / credentials the original app
already uses:

```bash
python3 aiops_sdk_demo_app/app.py
```

It listens on port **5002** by default (the original uses 5001,
`aiops_console_app` uses 5003), so all three can run at once:

```bash
python3 app.py                        # terminal 1 - the original, unchanged
python3 aiops_sdk_demo_app/app.py     # terminal 2 - this app, end users
python3 aiops_console_app/app.py      # terminal 3 - the AIOps team's console
```

## Who this app is for

This app is the end-user-facing side of a deliberate split. Someone
describing a problem here is never in a position to run a script against
production infrastructure - that authority lives in a separate app,
`aiops_console_app`, built for a different audience. See that app's README
for the other half of this story.

## What's identical vs. what's different

| | Original `app.py` | This app |
|---|---|---|
| UI (`static/index.html`) | original | a separate copy, since the "Check Known Fixes" behavior below differs |
| `/demo` login + upload pages | `week3.demo_app` blueprint | same blueprint, same import - not copied |
| Retrieval + suggestion (`/api/query`, mode: Suggest Similar Incidents) | direct calls to `query_incident.py` / `query_incident_gemini.py` | `sdk.suggest(text)` |
| Human verdict (`/api/verdict`) | direct calls to `log_feedback` / `escalate_to_developer` | `sdk.search.log_feedback(...)` / `sdk.servicenow.escalate(...)` |
| Known-issue remediation (`/api/query`, mode: Check Known Fixes) | `attempt_auto_remediation(text)` - matches, runs the real fix, resolves the incident, all in one request, no one asked | `sdk.raise_remedy_candidate(text)` - matches, and if confident, opens a real unresolved incident tagged for AIOps review. Runs nothing. Full stop. |
| Break/reset the demo scenarios | `mock_systems.py` | same module, same import - not copied |
| Which LLM answers | hardcoded to Gemini via `call_gemini` | still Gemini by default, but swappable |

Because it reuses the real `week3.mock_systems` module (not a copy), this
app and the original share the same on-disk demo state - breaking
`app_down` from either app's demo tray shows up in both.

## The status pill updates itself now

The `N resolved incidents · <model>` pill in the top-right reads
`/api/status`, which is a live BigQuery count - but it used to only load
once, on page load. Now it polls every 20 seconds, so if `aiops_console_app`
just approved a fix (which kicks off a corpus rebuild in the background -
see that app's README), the count here rises on its own without anyone
reloading this page. That's the direct answer to "why does the number stay
at 52 no matter how many incidents I resolve" - it wasn't a bug, the corpus
just wasn't being rebuilt at all until `aiops_console_app` started doing
that automatically.

## Proof it actually works

```bash
python3 aiops_sdk_demo_app/verify_app.py
```

9 checks, no real credentials needed. Two are worth calling out
specifically: one patches every remedy function to explode if called and
confirms `/api/query` (mode: Check Known Fixes) still returns cleanly
without tripping any of them - proving a confident match never runs
anything from this app. The other confirms the route this app used to have
for running a remedy (`/api/remedy/confirm`) no longer exists at all - not
disabled, not permission-gated, gone from the URL map entirely.

## The real change from the original: this app can't run a remedy, period

Earlier in this project, "human-in-the-loop" meant a person could approve a
fix inline, in the same browser tab that reported the problem. That's a real
improvement over running fixes with no one asked - but it still assumes the
person who noticed the symptom is the right person to authorize running a
script against production. That's not always true, and it's the reason this
app looks the way it does now.

With **mode: Check Known Fixes**, this app matches the incident against the
exact same catalog, with the exact same confidence threshold, as the
original. If it's confident, the *only* thing that happens is
`sdk.raise_remedy_candidate()` opens a real, unresolved ServiceNow incident
tagged `Auto-Remediation Candidate` and this app tells the person an
incident was raised and the AIOps team was notified. That's the entire
interaction - no approve button, no run button, nothing else this app can
do. The fix runs (or doesn't) entirely inside `aiops_console_app`, by
someone with a reason to be trusted with that decision. See that app's
README for what happens next to the incident this one raises.

If nothing matches confidently in the first place, this app falls back to
the same behavior the original already has for that case: an unresolved
ticket opens for a developer, same as before.

With **mode: Suggest Similar Incidents**, nothing changed - that path
already asked a person to confirm or correct a suggestion before anything
was logged, and it never ran an actual fix script to begin with.

## If you need to explain this in the demo

A few sentences that hold up under a follow-up question:

- **"What is this, exactly?"** - It's the same application, running a
  second time, with its backend logic pulled into one small internal
  library (`aiops_sdk`) instead of spread across five files.
- **"Why does that matter?"** - Today, if I wanted this logic somewhere
  else - a Slack bot, a scheduled job, a different UI - I'd copy files out
  of this project by name. The SDK gives that logic one stable object
  (`AiopsSDK`) instead, so reusing it is "import this," not "copy that file
  and hope the rest of the project doesn't change under it." This app and
  `aiops_console_app` are proof of exactly that reuse - two different
  applications, same SDK underneath.
- **"Does it still just run fixes on its own?"** - No, and not even with an
  inline approval button anymore. This app cannot run a remedy script at
  all - it can only raise a real incident and hand it to a separate app
  built for the team that owns the infrastructure. I can show that live:
  break a scenario, describe it here, then switch to the console app and
  watch it show up waiting for a decision.
- **"Who approves it, then?"** - Whoever has access to `aiops_console_app` -
  today that's anyone with the URL, since there's no login on either app
  yet. That's a real, named gap for a production rollout, not something I'm
  glossing over; see that app's README for what closing it would look like.
- **"Did you rebuild the logic?"** - No. Every method in the SDK is a
  one-line call into the exact function that already existed - verified
  with automated checks, not just by eye.
- **If a question goes past what you're sure of** - it's fine to say "that's
  a good next step I haven't built yet" rather than guess. All three
  READMEs in this project say plainly what's built vs. deliberately left
  for later - pointing to that is a legitimate answer, not a dodge.
