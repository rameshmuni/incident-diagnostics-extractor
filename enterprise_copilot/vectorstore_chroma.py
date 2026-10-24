"""
Thin wrapper around a local, on-disk ChromaDB collection - this project's
search index. Chroma stores everything (vectors + a sqlite metadata db)
inside CHROMA_PERSIST_DIR, right inside this project folder - nothing to
install as a separate service, nothing to run in the background.

Three functions, on purpose - add, search, count. If you ever swap Chroma
for a different vector store, write a new file with these same three
function signatures and change the one `import vectorstore_chroma as store`
line in query_incident.py, ingest_confluence.py, and ingest_servicenow.py.
"""
import chromadb

import config

_client = None
_collection = None


def get_collection():
    """Lazily open the on-disk Chroma database and the one collection this
    project uses - cached so every call after the first just reuses it."""
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(path=config.CHROMA_PERSIST_DIR)
        _collection = _client.get_or_create_collection(config.CHROMA_COLLECTION)
    return _collection


def add_records(ids, embeddings, texts, metadatas):
    """Insert or overwrite records by id. Safe to call again with the same
    ids later (e.g. re-running an ingest script) - Chroma's upsert replaces
    whatever was already stored under a matching id instead of duplicating it."""
    get_collection().upsert(
        ids=ids,
        embeddings=embeddings,
        documents=texts,
        metadatas=metadatas,
    )


def search_similar(query_embedding, k=None):
    """Returns the k closest records as a list of dicts, closest first:
    {"id": ..., "text": ..., "metadata": {...}, "distance": float}."""
    k = k or config.TOP_K
    result = get_collection().query(query_embeddings=[query_embedding], n_results=k)

    matches = []
    ids = result["ids"][0] if result["ids"] else []
    for i in range(len(ids)):
        matches.append({
            "id": result["ids"][0][i],
            "text": result["documents"][0][i],
            "metadata": result["metadatas"][0][i],
            "distance": result["distances"][0][i],
        })
    return matches


def count():
    """Total records currently stored - handy for a quick sanity check
    after running an ingest script."""
    return get_collection().count()


if __name__ == "__main__":
    # Quick manual check: `python vectorstore_chroma.py` should print the
    # current record count without needing any network access at all.
    print(f"Chroma collection '{config.CHROMA_COLLECTION}' has {count()} record(s) "
          f"at {config.CHROMA_PERSIST_DIR}")
