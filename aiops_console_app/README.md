# aiops_console_app

The AIOps team's own application - separate from both the original demo app
and `aiops_sdk_demo_app`, built for a different audience: whoever is
authorized to run a remedy script against real infrastructure, not whoever
merely reported a symptom.

## Why this exists

`aiops_sdk_demo_app`'s chat can recognize a known issue and open a real
ServiceNow incident for it - but it has no button and no code path capable
of running the actual fix (see that app's README). Something still has to
review what's waiting and decide whether it's safe to run. This app is that
something, and it is the **only** place in this entire project that ever
calls `sdk.run_confirmed_remedy_for_candidate()` - the one method anywhere
that both executes a remedy script and resolves an incident.

This answers the question raised earlier: who should be allowed to run a
remedy script - the person with the problem, or the team that owns the
system being touched? Here, concretely, it's the second one. An end user's
app can raise a candidate. Only this app can act on it.

## How the hand-off works

There is no shared Python process and no shared file between this app and
`aiops_sdk_demo_app` - the hand-off is a real ServiceNow incident:

1. Someone describes an incident in `aiops_sdk_demo_app`'s chat with mode
   set to "Check Known Fixes." If it confidently matches one of the 4 known
   issues, `sdk.raise_remedy_candidate()` opens a real, unresolved incident
   with `[AIOps Auto-Remediation Candidate]` written at the start of its
   `description` and returns immediately. Nothing has run yet.
2. This app's `/api/candidates` asks ServiceNow directly, on every page
   load, for every incident carrying that tag that isn't Resolved or Closed
   - no cache, no local queue, so it can never drift out of sync with what
   ServiceNow actually has open. (Earlier this filtered on `category`
   instead of a description tag, and on ServiceNow's `active` flag alone
   instead of also checking `state` - both turned out to be unreliable on
   this instance: `category` can be silently rejected/blanked outside its
   dropdown list, and `active` didn't flip false just because an incident
   was Resolved, only once it was Closed, so resolved candidates kept
   reappearing here as if still needing review. `list_remedy_candidates()`
   now excludes Resolved/Closed explicitly, in the query and again in
   Python, so this can't happen even if a given instance's `active` field
   doesn't behave the way stock ServiceNow assumes.)
3. Each one is shown with the original report, which known issue it
   matched, and a plain-language explanation of what approving it will
   actually do (`REMEDY_EXPLANATIONS` in `aiops_sdk/remediation.py`) -
   written for someone deciding whether to run it, not a report of
   something that already happened.
4. Clicking "Approve & Run Fix" calls `/api/candidates/confirm`, which calls
   `sdk.run_confirmed_remedy_for_candidate()` - runs the real remedy and
   resolves the *same* incident (by its `sys_id`), rather than creating a
   new one. Whoever raised the original incident sees it get resolved, same
   as any other ServiceNow ticket.

## The resolved-incident count updates itself now

Approving a fix here only ever touched ServiceNow - Week 2's "similar past
incidents" search (and the count shown in `aiops_sdk_demo_app`'s status
pill) reads from a separate BigQuery table that used to only get rebuilt by
manually running `fetch_resolved_incidents.py` then `build_bigquery_corpus.py`.
So a freshly-resolved incident wouldn't show up anywhere retrieval-related
until someone remembered to do that by hand - easy to miss, and confusing
in a demo where the count just... doesn't move.

Now, right after `/api/candidates/confirm` resolves an incident, this app
kicks off `sdk.search.refresh_corpus()` (see `aiops_sdk/README.md`) on a
background thread, so approving a fix doesn't sit there waiting on it.
`refresh_corpus()` only embeds incidents it hasn't seen before - it reads
which ones are already in the BigQuery table first, and skips those
entirely - so most refreshes finish in a few seconds, just long enough for
one paced Gemini call per newly-resolved incident. It's still backgrounded
on principle rather than run inline: the very first refresh ever against a
project has nothing to diff against yet, so that one run does embed the
whole existing backlog and can take a minute or more - after that, only
genuinely new incidents cost anything.

A banner at the top of this page polls `GET /api/refresh_status` and shows
what's happening - "refreshing" while it runs, then the new count once it's
done, or the error if it failed. `aiops_sdk_demo_app`'s status pill also now
polls every 20s instead of loading once, so its "N resolved incidents"
number visibly ticks up on its own once a refresh here finishes - no page
reload, no manually re-running scripts, no wondering whether the fix
"really" took effect.

There's also a **"Rebuild Search Corpus" button** in the top bar, calling
`POST /api/refresh_corpus`, that triggers the same rebuild directly - no
incident needs to be pending approval first. This exists because the
automatic trigger only fires on a fresh approval: if the rebuild logic
itself changes (as it did once already - see `aiops_sdk/retrieval.py`'s
`refresh_corpus()` for why it now asks ServiceNow for Resolved *or* Closed
incidents, not just Resolved), nothing re-runs it against what's currently
in ServiceNow until the next approval happens to come along. This button is
the way to force or test a rebuild on demand instead of waiting for one.

## How to run it

```bash
python3 aiops_console_app/app.py
```

Listens on port **5003** by default (the original app uses 5001,
`aiops_sdk_demo_app` uses 5002) - all three can run at once.

To see it do something: open `aiops_sdk_demo_app` (port 5002), break one of
the 4 demo scenarios from its `/demo` tray, switch its mode to "Check Known
Fixes," and describe that incident. Then open this app (port 5003) and hit
Refresh - the incident you just raised should be sitting there, waiting.

## Proof it actually works

```bash
python3 aiops_console_app/verify_console_app.py
```

7 checks, no real credentials needed - lists a mocked ServiceNow response
into the console's shape, confirms approving one runs the real remedy and
resolves the *same* incident (not a new one), confirms an unknown
`issue_id` is rejected rather than silently guessed at, and confirms the
background corpus refresh: that approving a fix triggers it, that it
updates `/api/refresh_status` correctly on both success and failure, and
that a second refresh never starts while one is already running.

## What this app deliberately doesn't have

No login, no role check - anyone with the URL can approve a fix here, same
as anyone with `aiops_sdk_demo_app`'s URL can report an incident. That's a
real, named gap, not an oversight: putting this behind whatever
authentication your org already uses for staff is the natural next step,
and nothing about `aiops_sdk` needs to change to add it - the `approved_by`
field already exists specifically so a real identity can be dropped in
there once one exists, in place of the free-text name field this prototype
uses today.

## If you need to explain this in the demo

- **"Why a third app, not just a button in the chat?"** - Because the
  person reporting a symptom and the person authorized to run a script
  against production infrastructure shouldn't be assumed to be the same
  person. Splitting them into two apps makes that boundary real instead of
  just a rule someone has to remember to follow.
- **"How do the two apps talk to each other?"** - They don't, directly. The
  hand-off is a real ServiceNow incident. This app doesn't trust anything
  the other app said in memory - it asks ServiceNow fresh, every time.
- **"What stops the end-user app from running a fix itself?"** - It doesn't
  have the code to do it. `sdk.run_confirmed_remedy_for_candidate()` is only
  ever called from this app's one route - that's not a permission check,
  it's a fact about which file contains that method call at all.
- **"Does approving a fix here actually make the assistant smarter?"** -
  Yes, and I can show it live: approve a fix, watch this page's banner say
  it's refreshing the search corpus, then switch to the chat app and watch
  its resolved-incident count go up on its own within about 20 seconds - no
  page reload, no manual script.
