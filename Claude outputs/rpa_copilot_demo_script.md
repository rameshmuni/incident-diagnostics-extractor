# RPA Copilot — Demo Script & Test Case Catalog

A ready-to-run script of chatbot queries and actions that exercises every feature currently built, grouped so each section proves a specific capability rather than just "it answers questions."

**Apps:**
- Chat (end-user): `https://aiops-sdk-demo-app-853524141068.us-central1.run.app/` — **RPA Copilot**
- Ops review console: `https://aiops-console-app-853524141068.us-central1.run.app/` — **RPA Copilot Console**

---

## 0. Pre-demo checklist

Run these before the audience sees anything, so nothing surprises you live.

1. Open the chat app and check the **"Check Known Fixes"** toggle exists and defaults to off (human-in-the-loop review mode).
2. Hit `/api/status` (or whatever shows it in the UI) and confirm `corpus_size` reads **50** and a model name is returned — this is your proof the backend, BigQuery, and Gemini are all actually live, not mocked.
3. In the chat app's demo tray, click **Reset** so all 4 mock systems start healthy.
4. Open the console app in a second tab/window so you can switch to it without fumbling for the URL mid-demo.

---

## 1. Feature map — what each part of the script proves

| Feature | Proven by |
|---|---|
| Live retrieval + generation (not canned answers) | Section 2 — status check + varied corpus questions |
| Semantic matching across real RPA scenarios | Section 2 — 18 questions spanning every incident category |
| Human-in-the-loop confirmation (confirm / correct / reject) | Section 3 |
| Honest escalation instead of guessing | Section 3.3 and Section 5 |
| Known-issue auto-detection on a *live, breakable* system | Section 4 |
| Separation of "report" vs "authorize a fix" (two different apps) | Section 4, steps 4.4–4.6 |
| End-to-end remediation that actually runs and resolves a ticket | Section 4, step 4.6 |
| Corpus self-refresh after a fix | Section 4, step 4.7 |
| Graceful handling of nonsense/out-of-scope input | Section 5 |

---

## 2. Corpus diagnosis — ask about real past incidents

Default mode (human-in-the-loop **on**). Type each into the chat. Expect: a ranked list of similar past incidents, a suggested root cause + resolution, then a confirm/correct/reject prompt (don't answer that yet — just look at the match quality for this section; Section 3 is where you act on the verdict).

Spread across every category in the corpus so the audience sees breadth, not one lucky match:

1. "A robot lost connection to Orchestrator in the middle of a transaction last night and the queue items are stuck."
2. "Our process can't find the selector on the customer portal right after the login step."
3. "The SAP automation can't find the Save button element on the GUI screen anymore."
4. "Workbook activities are failing with a file-in-use error on our Excel process a couple times a week."
5. "Three queue items have been stuck In Progress for 18 hours and the robot that owned them isn't running anymore."
6. "An unattended robot is failing to start — says no runtime license available even though we have unused seats."
7. "An Orchestrator credential asset is being rejected as invalid password, but it was working last week."
8. "Business rule failures are being retried three times and escalated as system errors instead of logged once."
9. "OCR extraction accuracy dropped hard this week on invoices from one specific vendor."
10. "Our Outlook automation throws a COM error saying it can't start Microsoft Outlook."
11. "A scheduled job just didn't trigger at its expected time last night."
12. "A new package version broke the process in Production right after we published it."
13. "VPN dropped mid-run and left partially entered data in a legacy system."
14. "A database lookup activity is timing out on one of our SQL queries."
15. "The UiPath Robot service keeps crashing — looks like high memory usage."
16. "Image-based selectors in our Citrix automation started failing after someone changed the screen resolution."
17. "Web scraping broke because the target site changed its table structure."
18. "A robot machine's screen was locked, so the unattended job couldn't run."

**Presenter note:** these are paraphrased the way a real person would type them, not copy-pasted from the corpus — that's deliberate, it shows semantic matching rather than string matching. If you want a guaranteed-highest-confidence example for a nervous first demo, lead with #1 or #5 — both use vocabulary very close to the stored RCA text.

---

## 3. The human-in-the-loop verdict loop

Pick any 3 results from Section 2 and actually act on them this time, to show all three branches:

### 3.1 Confirm
Ask: *"Robot unable to check out license - no available runtime license"* (close to RCA #6). When the suggestion comes back, click **Confirm**. Point out: this logs the feedback — a confirmed match is what trains future confidence, same mechanism the roadmap's Phase 2 confirm-rate metric is built on.

### 3.2 Correct
Ask something ambiguous that could plausibly match two different corpus entries, e.g.: *"Excel process failing intermittently with a file access error."* (could match RCA #4 or #39, both Excel-lock incidents). If the bot picks one, use **Correct** to pick the other match instead. Point out: a wrong first guess doesn't become truth — a human still has the final say.

### 3.3 Reject / none apply
Ask something plausible-sounding but genuinely not in the corpus, e.g.: *"Our process fails because the target application requires a hardware security key that isn't plugged in."* Click **None of these apply**. Expect: a real new incident gets created in ServiceNow and escalated to a developer — show the returned incident number. This is the "it doesn't guess when it doesn't know" proof point.

---

## 4. Live remediation — break something real, then fix it end to end

This is the strongest section of the demo: an actual mock system breaks, the bot diagnoses it, and a human approves a real fix that actually runs.

1. In the chat app's demo tray, click **Break** on **"App Down."**
2. Switch the chat to **"Check Known Fixes"** mode (toggle off human-in-the-loop).
3. Ask: *"The application is completely down, the page won't load for anyone and just times out."*
   Expect: a confident match against the known catalog, and the response **"raised for AIOps review"** with a new ServiceNow incident number — note explicitly that no fix has run yet.
4. Switch to the **console app** tab.
5. Click **refresh candidates** — the incident from step 3 should appear in the open list.
6. Click it, review the proposed remedy, then **Approve & run fix**.
7. Point out what just happened: this is the *only* button, in the *only* app, anywhere in this system, that executes a real remedy script and resolves a ServiceNow ticket — the chat app itself has no code path that can do this, by design.
8. Go back to the chat app's demo tray and show **"App Down"** is now healthy again.
9. Optionally: show `/api/refresh_status` or the console's refresh indicator — the corpus is re-embedding the just-resolved incident in the background so it's searchable immediately, not after some batch job runs later.

Repeat the same 1–7 flow with any of the other 3 breakable issues for a second pass if you have time:
- **"Login UI Disabled"** → ask: *"The Sign In button on the login page is greyed out and nobody can log in at all."*
- **"Login Cred Backend"** → ask: *"Users are typing the right password but sign-in still fails — looks like it can't verify credentials against the backend."*
- **"Upload Permission"** → ask: *"A user tries to upload a file and it fails with a permission error — can't write to the storage folder."*

---

## 5. Edge cases — proving it doesn't bluff

Ask these to show the bot fails safely instead of hallucinating a confident-sounding wrong answer:

1. *"asdkjasd aaaa 12345 ???"* — gibberish. Expect: no confident match, escalated or a clear "nothing matched" response, not a fabricated root cause.
2. *"What's the weather like today?"* — fully out of domain. Expect the same honest non-match behavior.
3. An empty submit (just hit send with no text) — expect a clean `"Incident text is required"` validation message, not a crash.

---

## 6. Quick talking points if asked follow-up questions

- **"Is this really live, or pre-recorded matches?"** → Point back to the `/api/status` corpus-size check from Section 0, and note every answer above came from a different phrasing than what's stored — that's retrieval + generation, not lookup.
- **"What stops it from running a wrong fix automatically?"** → Section 4's two-app split: the chat app can only *request* a fix; only a person in the console app can approve one, and only for the handful of catalog issues with a real tested remedy script behind them.
- **"What happens to something totally novel it's never seen?"** → Section 3.3 and Section 5 — it escalates to a real developer ticket instead of guessing.

---

## Appendix — full 50-record corpus reference

Use this if the audience throws out their own scenario and you want to know whether it's in-corpus before asking live. (Paraphrase, don't read verbatim, for the same semantic-matching reason noted in Section 2.)

| # | Category | Priority | Short description |
|---|---|---|---|
| 1 | Software | P2 | Unattended robot lost connection to Orchestrator mid-transaction |
| 2 | Software | P3 | Selector not found on customer web portal after login step |
| 3 | Software | P2 | Selector not found on SAP GUI transaction screen |
| 4 | Software | P3 | Excel file locked, workbook activities failing intermittently |
| 5 | Software | P2 | Queue item stuck In Progress after robot crash |
| 6 | Software | P2 | Robot unable to check out license - no available runtime license |
| 7 | Software | P3 | Orchestrator asset credential rejected - invalid password |
| 8 | Software | P4 | REFramework misclassifying business exceptions as system exceptions |
| 9 | Software | P3 | OCR extraction returning garbled text from scanned invoices |
| 10 | Software | P3 | Outlook COM error - Cannot start Microsoft Outlook |
| 11 | Software | P3 | Scheduled job did not trigger at expected time |
| 12 | Software | P2 | New package version broke process in Production after publish |
| 13 | Network | P2 | VPN dropped mid-run leaving partial data entered in legacy system |
| 14 | Database | P3 | SQL query timeout in database lookup activity |
| 15 | Software | P3 | UiPath Robot service crashing due to high memory usage |
| 16 | Software | P3 | Citrix image-based selector failing after screen resolution change |
| 17 | Software | P4 | Data scraping breaking due to website table structure change |
| 18 | Software | P2 | Robot machine screen locked, unattended job could not run |
| 19 | Software | P3 | Windows update broke file path automation after path change |
| 20 | Software | P3 | Third-party shipping API returning rate-limit errors |
| 21 | Software | P3 | Robot left in 'busy' state after orphaned process, blocking new jobs |
| 22 | Software | P2 | Robot machine disk full, job logs failing to write |
| 23 | Software | P3 | Orchestrator API calls failing with 401 after token expiry |
| 24 | Software | P4 | Queue retries exhausted - same business exception looping |
| 25 | Software | P2 | Config.xlsx values mismatched between Dev and Prod after migration |
| 26 | Software | P3 | Coordinate-based click missing target after screen resolution change |
| 27 | Software | P3 | Global exception handler not catching custom exception type |
| 28 | Network | P2 | Robot unable to reach network share for input files |
| 29 | Software | P3 | UiPath Assistant not starting automatically on machine reboot |
| 30 | Software | P3 | Java-based application elements not detected by UI Automation |
| 31 | Software | P4 | Macro security warning blocking automated Excel workbook |
| 32 | Software | P2 | Two robots processing the same queue item due to race condition |
| 33 | Software | P3 | Orchestrator storage bucket access denied for output files |
| 34 | Software | P2 | Two-factor authentication prompt blocking unattended login |
| 35 | Software | P2 | Antivirus quarantined UiPath package executable |
| 36 | Software | P2 | Robot unresponsive - Orchestrator connection lost overnight |
| 37 | Software | P3 | Selector not found on internal HR portal after rebranding update |
| 38 | Software | P3 | SAP session not found - multiple SAP windows open |
| 39 | Software | P3 | Excel file locked - second occurrence, different shared folder |
| 40 | Software | P2 | Business exception queue growing unmonitored, SLA breach risk |
| 41 | Software | P3 | Dynamic web element ID changed after application update, selector break |
| 42 | Software | P3 | Robot machine time drift causing scheduled job to run twice |
| 43 | Software | P4 | PDF extraction activity crashing on password-protected files |
| 44 | Software | P3 | Robot license conflict between attended and unattended sessions |
| 45 | Software | P3 | REFramework InitAllApplications step timing out on slow VDI |
| 46 | Software | P4 | Attachment upload failing - file name contains special characters |
| 47 | Software | P3 | Orchestrator dashboard showing stale robot status |
| 48 | Software | P4 | Currency formatting mismatch causing downstream validation failure |
| 49 | Software | P3 | Robot process consuming excessive CPU, slowing down shared VDI |
| 50 | Software | P4 | Orchestrator folder-level permissions blocking process visibility |
