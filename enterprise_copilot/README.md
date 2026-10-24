# Enterprise Incident Copilot (Daisy LLM + ChromaDB + ServiceNow + Confluence)

A plain-Python replica of the GCP demo project (RAG incident diagnosis +
human-in-the-loop verdict + ServiceNow escalation), rebuilt for an
office/enterprise setup:

- **Daisy LLM** instead of Gemini (chat + embeddings)
- **ChromaDB** (local, on-disk) instead of BigQuery vector search
- **ServiceNow** - same idea as before, config-driven
- **Confluence** added as a second knowledge source, alongside resolved
  ServiceNow incidents

No SDK, no classes, no facade objects - every file is a short script with a
handful of plain functions. The goal is that you can open any file here and
understand (and change) it on your own, without needing an AI assistant in
the loop, because you mentioned you won't have Claude access in the office.

## Files, and what each one does

| File | What it does |
|---|---|
| `config.py` | Loads `.env` and exposes every setting as a plain constant. The one file to edit when you want to change a model name, URL, or tuning parameter. |
| `.env.example` | Every setting, documented inline. Copy to `.env` and fill in real values - `.env` is gitignored. |
| `llm_daisy.py` | One function, `chat_completion(prompt)` - talks to Daisy's chat endpoint. |
| `embeddings_daisy.py` | One function, `embed_text(text)` - talks to Daisy's embeddings endpoint. |
| `vectorstore_chroma.py` | Three functions - `add_records()`, `search_similar()`, `count()` - wrapping a local ChromaDB collection. |
| `servicenow_client.py` | `create_incident()`, `resolve_incident()`, `escalate()`, `list_open()`, `list_resolved_incidents()`. |
| `ingest_confluence.py` | Pulls pages from your Confluence spaces, chunks them, embeds them, loads them into Chroma. Run whenever your Confluence KB changes. |
| `ingest_servicenow.py` | Pulls resolved ServiceNow incidents, embeds them, loads them into the *same* Chroma collection as Confluence. Run whenever you want recently-resolved incidents folded in. |
| `query_incident.py` | The main flow: embed a new incident → search Chroma → ask Daisy for a suggestion → human confirms/corrects/rejects → logs the verdict (and escalates to ServiceNow on reject). Run it directly for an interactive CLI, or `import` `suggest()` / `log_feedback()` into your own script or web app. |

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# now open .env and fill in: Daisy base URL + API key, ServiceNow instance/
# creds, Confluence URL + personal access token + space keys
```

## Running it

```bash
# 1. Load your knowledge base into Chroma (run either or both, any time):
python ingest_servicenow.py
python ingest_confluence.py

# 2. Ask it something:
python query_incident.py
```

`query_incident.py` will show you the closest matches, Daisy's suggested
root cause, and then ask you to confirm it, correct it, or say none of them
apply (which opens a real ServiceNow ticket). Whatever you decide gets
logged to `feedback_log.jsonl` - if you re-ask the *exact same* question
later, the correction is reused automatically instead of asking Daisy again
and possibly getting the same wrong answer.

Each file also runs standalone as a quick smoke test, e.g.:

```bash
python vectorstore_chroma.py   # prints how many records are in Chroma right now
python servicenow_client.py    # prints how many resolved incidents it can see
python llm_daisy.py            # sends one test prompt, prints the reply
python embeddings_daisy.py     # embeds one test string, prints the vector length
```

## How to swap a provider later

Every swappable piece (LLM, embeddings, vector store, ticketing system) is
isolated in its own file, and every other file only ever calls that file's
small set of plain functions - never anything internal to it. To swap one
out:

1. Copy the relevant file (e.g. `llm_daisy.py` → `llm_openai.py`).
2. Change the request/response handling inside it to match the new
   provider's actual API. The two lines that usually need to change are
   marked with `# <- change if ... differs` comments.
3. Keep the function name and signature identical (e.g. `chat_completion(prompt, ...) -> str`).
4. Change the one `import llm_daisy as llm` line (in `query_incident.py`)
   to `import llm_openai as llm`.

That's it - nothing else in the project needs to know a provider changed.
The same recipe works for:

- **LLM**: `llm_daisy.py` → `chat_completion(prompt) -> str`
- **Embeddings**: `embeddings_daisy.py` → `embed_text(text) -> list[float]`
- **Vector store**: `vectorstore_chroma.py` → `add_records()`, `search_similar()`, `count()`
- **Ticketing system**: `servicenow_client.py` → `create_incident()`, `resolve_incident()`, `escalate()`, `list_open()`, `list_resolved_incidents()`

## Tuning knobs (all in `.env`, no code changes needed)

- `TOP_K` - how many matches to retrieve per question (default 3)
- `CHUNK_SIZE_CHARS` / `CHUNK_OVERLAP_CHARS` - how Confluence pages get
  split before embedding (default 1500 / 200 characters)
- `CHROMA_PERSIST_DIR` / `CHROMA_COLLECTION` - where Chroma stores its data
  and what the collection is called
- `SN_AUTH_MODE` - `basic` (most ServiceNow instances) or `oauth_password`
  (only if Basic Auth is disabled for the REST API on your instance)
- `CONFLUENCE_CLOUD` - `true` for a `*.atlassian.net` site, `false` for
  Data Center/Server

## Known limitations (carried over honestly from the GCP demo project)

- `feedback_log.jsonl` is a local file - if you move this to a server that
  gets redeployed or restarted often, a correction could be lost between
  restarts. In the GCP version this was eventually fixed by moving the log
  into BigQuery; here, the equivalent fix would be a small database table
  or a second Chroma collection - not done yet, since this is a fresh
  office setup that's presumably running on one machine for now.
- `parse_pick()` finding the model's chosen match is a plain substring
  check on the LLM's own text, not a structured output - it falls back to
  the closest-distance match if it can't find a clear pick. Good enough for
  a first pass; a structured/JSON response from Daisy would make this more
  reliable if its API supports that.
- Confluence chunking is plain fixed-size character splitting, not
  paragraph- or heading-aware. Simple and fast; if retrieval quality on
  long pages is poor, that's the first thing worth improving.
