import json
from pathlib import Path

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

CORPUS_FILE = "resolved_incidents_corpus.json"
INDEX_FILE = "incident_index.faiss"
METADATA_FILE = "incident_index_metadata.json"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"


def load_corpus(path=CORPUS_FILE):
    # the flat records fetch_resolved_incidents.py already prepared
    return json.loads(Path(path).read_text())


def embed_records(records, model_name=EMBEDDING_MODEL):
    # one vector per record's combined short_description + close_notes text
    model = SentenceTransformer(model_name)
    texts = [r["text"] for r in records]
    vectors = model.encode(texts, show_progress_bar=True, convert_to_numpy=True)
    return vectors.astype(np.float32)


def build_index(vectors):
    # flat L2 index is plenty for 50 records - no need for anything fancier at this scale
    dimension = vectors.shape[1]
    index = faiss.IndexFlatL2(dimension)
    index.add(vectors)
    return index


def main():
    # entry point: embed the corpus once, save the index and its matching metadata to disk
    records = load_corpus()
    vectors = embed_records(records)
    index = build_index(vectors)

    faiss.write_index(index, INDEX_FILE)
    Path(METADATA_FILE).write_text(json.dumps(records, indent=2))

    print(f"Indexed {index.ntotal} record(s), dimension {vectors.shape[1]}")
    print(f"Saved index to {INDEX_FILE} and metadata to {METADATA_FILE}")


if __name__ == "__main__":
    main()
