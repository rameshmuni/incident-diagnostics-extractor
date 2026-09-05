# Learning Plan: Going FAISS-free — Gemini Embeddings + BigQuery Vector Search

Your manager's new ask, precisely: host entirely on Cloud Run, drop FAISS, use a Google product for embeddings, use a Google product for vector storage, and use a Google LLM. One piece of good news up front — the LLM part is already done. `query_incident_gemini.py` and `app.py` already call Gemini for the reasoning step. What's actually changing is the other two-thirds of the pipeline: **how text becomes a vector**, and **where those vectors live**.

This plan builds on last time's FAISS/sentence-transformers session — same style, but for the two pieces that are moving. Skim your own notes from that session first if today's terms ("embedding," "vector," "distance") feel rusty; this one assumes you've got that foundation and layers the GCP-specific pieces on top.

**One naming note before we start:** Google renamed "Vertex AI" to the **Gemini Enterprise Agent Platform** back in May 2026. You'll see both names still floating around in blog posts and search results — same underlying service, new branding. It matters here only as background, because the design below deliberately avoids needing that platform at all (more on why in Block 2).

---

## What's changing, mapped to your actual files

| Today (FAISS-based) | Tomorrow (Google-native) |
|---|---|
| `sentence-transformers` (`all-MiniLM-L6-v2`), runs locally, 384-dim vectors | Gemini's embedding model, called over the network via `google-genai` (same library `query_incident_gemini.py` already imports), up to 3072-dim vectors |
| `faiss.IndexFlatL2`, saved to `incident_index.faiss` on disk, baked into the Docker image | A BigQuery table (`incident_corpus`), embeddings stored as a column, queried live — nothing baked into the image |
| `build_faiss_index.py` writes local files | A new build script writes/replaces rows in BigQuery |
| `run_refresh.py` → rebuild FAISS file → **redeploy the whole container** | `run_refresh.py` → write new rows to BigQuery → **nothing to redeploy** |
| `query_incident.py`'s `load_index()` / `search()` | New `load`/`search` that runs a `VECTOR_SEARCH` SQL query |

That last row is the thing worth sitting with: this migration doesn't just satisfy the "no FAISS" instruction, it also fixes the exact problem we hit at the end of the last session — refreshing the corpus currently means rebuilding and redeploying the container image. With BigQuery, refreshing the corpus is just `INSERT`/`DELETE` statements against a table. The Cloud Run *service* never has to change.

---

## Block 1 (35 min) — Gemini embeddings, and why they're not a drop-in twin of your current model

### The model itself

The current Gemini embedding model is `gemini-embedding-2`, called through the same `google-genai` client you already use for generation:

```python
from google import genai

client = genai.Client()  # same GEMINI_API_KEY env var pattern as call_gemini()
result = client.models.embed_content(
    model="gemini-embedding-2",
    contents="Robot BOT-FIN-03 lost connection during the nightly reconciliation run.",
)
vector = result.embeddings[0].values
```

Compare that to today's `embed_query()` in `query_incident.py`:

```python
_embedding_model_cache = None

def embed_query(text, model_name=EMBEDDING_MODEL):
    global _embedding_model_cache
    if _embedding_model_cache is None:
        _embedding_model_cache = SentenceTransformer(model_name)
    vector = _embedding_model_cache.encode([text], convert_to_numpy=True)
    return vector.astype(np.float32)
```

Structurally almost the same shape — text in, vector out. The caching pattern even carries over (you'll still want a cached `genai.Client()`, not one built per request). But two real differences matter:

1. **Local inference vs. a network call.** `SentenceTransformer` runs the model on your own CPU — no network, no per-call latency, works offline. Calling Gemini's embedding endpoint means every single embed is an HTTPS round-trip to Google, subject to network latency and rate limits, and your incident text leaves your infrastructure on every call (same tradeoff you already accepted for generation when you added `query_incident_gemini.py` — this just extends it to the retrieval half too).
2. **Dimensions.** `all-MiniLM-L6-v2` always outputs 384 numbers. `gemini-embedding-2` defaults to 3072, but is explicitly designed to be truncated to smaller sizes — Google's own docs recommend 768, 1536, or 3072 rather than the full 3072 for most uses. Whatever size you pick, **it must be fixed and consistent** for every vector you ever store or query — the corpus embeddings and the query embedding have to be produced with the same model and dimension, exactly like today, or "closest vector" stops meaning anything.

### Why this satisfies "use a Google product for embeddings" cleanly

Because it reuses the exact API key and client you already have working (`GEMINI_API_KEY`, `google-genai`), there's no new GCP project wiring, no service account, no IAM to set up just to get embeddings. That's a deliberate simplification — Google also offers embedding generation *inside* BigQuery itself via `AI.EMBED` / `AI.GENERATE_EMBEDDING`, but those functions route through a BigQuery-to-Gemini-Enterprise-Agent-Platform remote connection, which means provisioning a connection resource and granting it IAM access before you can run a single query. Calling Gemini directly from Python and just storing the resulting numbers in BigQuery sidesteps all of that — same end result (a Google model produced the vector), much less plumbing to learn and maintain. This is the choice we made together for today's session.

### Self-check

- Why can't you mix vectors from `all-MiniLM-L6-v2` and `gemini-embedding-2` in the same search?
- What's the actual cost of calling an embedding API instead of running the model locally — name two.
- If you truncate `gemini-embedding-2` to 768 dimensions, does that break the "same model, same dimension" rule? Why or why not?

---

## Block 2 (40 min) — BigQuery as the vector store

### What BigQuery vector search actually is

FAISS is a library your process loads and holds vectors in memory. BigQuery is a hosted, serverless data warehouse — the vectors live as a normal column in a normal table, and you ask for the nearest ones with a SQL function instead of a library call. No server to run, no index file to bake into a container; you pay for storage (cents per GB per month) and for the bytes scanned per query, and at your corpus size (tens to low hundreds of incidents) both of those round to negligible.

The table shape is simple — basically your current `incident_index_metadata.json`, plus one more column:

```sql
CREATE TABLE `your_project.incident_assistant.incident_corpus` (
  incident_id STRING,
  sys_id STRING,
  short_description STRING,
  category STRING,
  text STRING,
  embedding ARRAY<FLOAT64>
);
```

Each row is one resolved incident: the same fields `fetch_resolved_incidents.py` already produces, plus the `embedding` array that used to live only inside the FAISS binary file.

### Running the actual search

This is the direct replacement for `index.search(query_vector, k)`:

```sql
SELECT
  base.incident_id, base.short_description, base.text,
  distance
FROM
  VECTOR_SEARCH(
    TABLE `your_project.incident_assistant.incident_corpus`,
    'embedding',
    (SELECT [0.013, -0.041, ...] AS embedding),  -- the new incident's vector, computed in Python
    top_k => 3,
    distance_type => 'COSINE',
    options => '{"use_brute_force":true}'
  );
```

Two things worth calling out deliberately:

- **`use_brute_force: true`.** BigQuery supports building an ANN (approximate nearest neighbor) `VECTOR INDEX` on the table for speed at scale — but Google's own docs are explicit that without one, `VECTOR_SEARCH` falls back to brute force: it checks every row and returns exact results. That's precisely what `IndexFlatL2` does today, and at 50–200 rows it's instant either way. **We don't need a vector index for this corpus size**, and skipping it means one less GCP resource to create, tune, or explain in a review — same reasoning as "flat index is plenty at 51 records" from the FAISS session, just one layer up the stack.
- **`distance_type => 'COSINE'`.** Update from what I said earlier — your manager's right to steer here, and this isn't just a style preference. FAISS's `IndexFlatL2` used Euclidean distance because that's what a bare `IndexFlatL2` defaults to, not because Euclidean was chosen deliberately for text similarity. Gemini's embedding model, by contrast, is explicitly documented as producing **unit-normalized vectors** (the full 3072-dim output is always normalized, and truncated sizes like 768 or 1536 get auto-normalized too), and Google's own guidance for these embeddings is to use cosine similarity — it measures the *angle* between two vectors (direction, i.e. meaning) rather than Euclidean distance, which also picks up differences in vector *length* that don't actually correspond to "less similar meaning." Cosine is the more correct choice for this model, not just a swap-in-name-only replacement for what FAISS happened to use.

  On your manager's second suggestion, dot product: worth knowing this isn't really a third independent option here. For **unit-normalized vectors specifically**, cosine similarity and dot product produce the exact same ranking — dot product of two unit vectors *is* their cosine similarity, just without the extra normalization step BigQuery's `COSINE` does internally. Since Gemini's embeddings are normalized either way, `COSINE` and `DOT_PRODUCT` would return the same top-3 matches in the same order here; `COSINE` is used below because it's self-documenting (it's explicit that direction, not magnitude, is what's being compared) and stays correct even if a future embedding model isn't pre-normalized, whereas `DOT_PRODUCT` would silently start ranking wrong the moment that assumption stopped holding.

### How a query actually reaches BigQuery from Python

You won't hand-write that SQL string with a literal array pasted in — the Python `google-cloud-bigquery` client runs it as a parameterized query, passing the just-computed embedding as a query parameter, and returns rows the same way `search()` returns dicts today:

```python
from google.cloud import bigquery

def search(client, query_vector, k=3):
    query = """
        SELECT base.incident_id, base.short_description, base.text, distance
        FROM VECTOR_SEARCH(
            TABLE `your_project.incident_assistant.incident_corpus`,
            'embedding',
            (SELECT @query_vector AS embedding),
            top_k => @k,
            distance_type => 'COSINE',
            options => '{"use_brute_force":true}'
        )
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("query_vector", "FLOAT64", query_vector),
        bigquery.ScalarQueryParameter("k", "INT64", k),
    ])
    return [dict(row) for row in client.query(query, job_config=job_config).result()]
```

Notice the shape: this is almost a mechanical swap for the current `search(index, metadata, query_vector, k)` — same inputs conceptually, same "list of dicts with the matched text and a distance" output. `build_prompt()`, `parse_slm_pick()`, the human verdict flow, the feedback logging — none of that has to change at all. The migration is isolated to the retrieval layer, which is exactly why it's safe to do without touching the reasoning half of the app.

### Self-check

- Why is a `VECTOR INDEX` unnecessary at this corpus size, and what would change your mind about that?
- What does `options => '{"use_brute_force":true}'` actually buy you, in the same terms `IndexFlatL2` bought you before?
- Why is cosine similarity the better fit for Gemini's embeddings specifically, rather than just "whatever FAISS happened to use before"?
- Why do `COSINE` and `DOT_PRODUCT` give the same ranked results here, and what property of the embeddings makes that true?

---

## Block 3 (25 min) — The end-to-end shape of the new pipeline

Walk through this against your actual files, same as last time:

1. **`fetch_resolved_incidents.py`** — unchanged. Still pulls resolved incidents from ServiceNow into the same flat record shape.
2. **A new build step** (replaces `build_faiss_index.py`) — for each record, call Gemini's `embed_content()` to get its vector, then write all the rows (text fields + embedding) into the BigQuery `incident_corpus` table, replacing whatever was there before.
3. **`run_refresh.py`** — same two-step shape it has today (`fetch` then `build`), except step 2 now ends in "rows updated in BigQuery" instead of "files written to local disk." This is what actually gets rid of the "rebuild-the-image-to-refresh-data" problem.
4. **A new query step** (replaces `load_index()` + `search()` in `query_incident.py`) — embed the new incident with Gemini, run the `VECTOR_SEARCH` query above, get back the same shape of match data `build_prompt()` already expects.
5. **`build_prompt()`, `call_gemini()`, `parse_slm_pick()`, `ask_human_verdict()` / the `/api/verdict` route, `log_feedback()`, `escalate_to_developer()`** — genuinely untouched. This is the part worth saying out loud to your manager: the migration is contained entirely to "how do we find similar past incidents," not a rewrite of the whole assistant.
6. **`app.py`** — same routes, same JSON shapes in and out. Only the internals of `api_query()`'s retrieval call change.
7. **Dockerfile / `requirements.txt`** — this is where FAISS actually disappears: `faiss-cpu`, `numpy`, and `sentence-transformers` come out of `requirements.txt`; `google-cloud-bigquery` goes in. No more `incident_index.faiss` / `incident_index_metadata.json` files to `COPY` into the image at all — one more reason this is a smaller, not larger, Docker image than what you have now.

### Self-check — narrate it out loud (this is the one your manager will actually ask for)

*"A new incident's text gets turned into a vector by Gemini's embedding model over the API — same idea as before, but a hosted model instead of one running locally. That vector gets sent to BigQuery as a query parameter, which runs a brute-force nearest-neighbor search against however many resolved incidents are stored there and returns the closest three, exact same shape of result FAISS used to hand back. Everything downstream — building the prompt, asking Gemini to reason over those three past incidents, the human confirming or correcting the answer — is completely unchanged. The only things that moved are where the vectors get computed and where they live: off a local model and a local file, onto two Google services that don't need a container rebuild to update."*

---

## Rehearsal Q&A — likely follow-ups from your manager

- **"Why BigQuery instead of Vertex AI Vector Search / the Gemini Enterprise Agent Platform's vector store?"** — That service is purpose-built for large-scale ANN search (millions of vectors, sub-millisecond latency) and requires standing up a deployed index endpoint that costs money even while idle. At 50–200 incidents, BigQuery's brute-force `VECTOR_SEARCH` gives exact (not approximate) results, costs pennies, and needs zero standing infrastructure — the right-sized tool, not the biggest one available.
- **"Doesn't this mean an extra network call on every query now, for embeddings and for search?"** — Yes, both retrieval steps now go over the network instead of running in-process. That's the real cost of "entirely on Cloud Run, no self-hosted models" — traded deliberately for zero server management and no GPU/CPU cost for hosting an embedding model ourselves.
- **"What happens to the old FAISS files?"** — They stop being produced entirely. `build_faiss_index.py` gets replaced, and the Dockerfile no longer copies any local index files into the image — one less thing baked into every deploy.
- **"Is this fully 'Google products end to end' now?"** — Embeddings: Gemini. Vector storage and search: BigQuery. Reasoning/LLM: Gemini. Hosting: Cloud Run. Corpus source: ServiceNow (that one's not going anywhere — it's where the incidents actually live).

---

## What's next

Once this makes sense well enough to explain without notes, the next session is the actual code: writing the BigQuery build/query scripts, updating `requirements.txt` and the Dockerfile, and testing the new retrieval path end to end before it goes anywhere near a redeploy.
