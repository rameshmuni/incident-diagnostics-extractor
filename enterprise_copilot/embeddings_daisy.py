"""
Talks to Daisy LLM's embeddings endpoint (OpenAI-compatible, Bearer auth).

Same idea as llm_daisy.py: one function, one job, one file that knows the
exact request/response shape. To swap embeddings providers later, copy this
file, change the two lines marked below, and change the one
`import embeddings_daisy as embedder` line in the files that use it
(query_incident.py, ingest_confluence.py, ingest_servicenow.py).
"""
import requests

import config


def embed_text(text):
    """Turn one string into one embedding vector (a list of floats)."""
    url = f"{config.DAISY_EMBEDDING_BASE_URL.rstrip('/')}/embeddings"  # <- change if Daisy's path differs
    headers = {
        "Authorization": f"Bearer {config.DAISY_EMBEDDING_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": config.DAISY_EMBEDDING_MODEL,
        "input": text,
    }

    resp = requests.post(url, headers=headers, json=body, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"Daisy embeddings request failed: {resp.status_code}\n{resp.text[:500]}")

    data = resp.json()
    try:
        # Standard OpenAI-compatible shape: {"data": [{"embedding": [...]}]}
        return data["data"][0]["embedding"]  # <- change if Daisy's response shape differs
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected Daisy embeddings response shape: {data}") from exc


if __name__ == "__main__":
    # Quick manual check: `python embeddings_daisy.py` should print a vector length.
    vec = embed_text("The robot lost connection to Orchestrator.")
    print(f"Got a {len(vec)}-dimension vector. First 5 values: {vec[:5]}")
