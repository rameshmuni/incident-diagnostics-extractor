"""
Pulls resolved ServiceNow incidents and loads them into the Chroma
knowledge base, alongside whatever Confluence content ingest_confluence.py
already loaded - both land in the same collection, so one search covers
both kinds of source.

Run it whenever you want today's resolved incidents folded in:
    python ingest_servicenow.py
"""
import servicenow_client as sn
import embeddings_daisy as embedder
import vectorstore_chroma as store


def main():
    incidents = sn.list_resolved_incidents()
    incidents = [i for i in incidents if i.get("close_notes")]  # nothing to learn from without a write-up
    print(f"Fetched {len(incidents)} resolved incident(s) with close notes.")

    if not incidents:
        print("Nothing to ingest.")
        return

    ids, texts, metadatas = [], [], []
    for inc in incidents:
        text = f"{inc['short_description']}\n\n{inc['close_notes']}"
        ids.append(f"servicenow-{inc['number']}")
        texts.append(text)
        metadatas.append({
            "source": "servicenow",
            "incident_id": inc["number"],
            "category": inc.get("category") or "",
        })

    print(f"Embedding {len(texts)} incident(s)...")
    embeddings = []
    for i, text in enumerate(texts, start=1):
        embeddings.append(embedder.embed_text(text))
        if i % 10 == 0 or i == len(texts):
            print(f"  embedded {i}/{len(texts)}")

    store.add_records(ids, embeddings, texts, metadatas)
    print(f"Done. Chroma collection now has {store.count()} record(s) total.")


if __name__ == "__main__":
    main()
