"""
Pulls pages from your Confluence spaces and loads them into the Chroma
knowledge base, chunked and embedded.

Run it whenever your Confluence KB changes:
    python ingest_confluence.py

Uses LangChain's ConfluenceLoader to do the actual Confluence REST calls -
see https://python.langchain.com/docs/integrations/document_loaders/confluence
for other options it supports (label filters, specific page-id lists,
attachments, comments...). This file only uses the common case: "give me
every page in these spaces."
"""
from langchain_community.document_loaders import ConfluenceLoader

import config
import embeddings_daisy as embedder
import vectorstore_chroma as store


def chunk_text(text, size=None, overlap=None):
    """Plain fixed-size character chunking with overlap - no NLP, no
    sentence-boundary detection. Simple on purpose: easy to read, easy to
    tune via CHUNK_SIZE_CHARS / CHUNK_OVERLAP_CHARS in .env, and good enough
    for this kind of retrieval. A long Confluence page becomes several
    chunks so one page's irrelevant section doesn't drown out the relevant
    paragraph when it's embedded."""
    size = size or config.CHUNK_SIZE_CHARS
    overlap = overlap or config.CHUNK_OVERLAP_CHARS
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap  # step back by `overlap` so neighboring chunks share context
    return chunks


def load_one_space(space_key):
    """One ConfluenceLoader per space - space_key is a constructor
    argument, not a load()-time argument, in the installed langchain-community
    version this was written against."""
    if config.CONFLUENCE_CLOUD:
        # Confluence Cloud: email + API token
        loader = ConfluenceLoader(
            url=config.CONFLUENCE_URL,
            username=config.CONFLUENCE_USERNAME,
            api_key=config.CONFLUENCE_PAT,
            cloud=True,
            space_key=space_key,
            limit=50,
            max_pages=2000,
        )
    else:
        # Confluence Data Center / Server: Personal Access Token, no username needed
        loader = ConfluenceLoader(
            url=config.CONFLUENCE_URL,
            token=config.CONFLUENCE_PAT,
            cloud=False,
            space_key=space_key,
            limit=50,
            max_pages=2000,
        )
    return loader.load()


def main():
    if not config.CONFLUENCE_URL or not config.CONFLUENCE_SPACE_KEYS:
        print("CONFLUENCE_URL / CONFLUENCE_SPACE_KEYS not set in .env - nothing to do.")
        return

    space_keys = [s.strip() for s in config.CONFLUENCE_SPACE_KEYS.split(",") if s.strip()]
    all_docs = []
    for space_key in space_keys:
        print(f"Fetching Confluence space '{space_key}'...")
        docs = load_one_space(space_key)
        print(f"  -> {len(docs)} page(s)")
        all_docs.extend(docs)

    if not all_docs:
        print("No Confluence pages found - check CONFLUENCE_SPACE_KEYS and your token's access.")
        return

    ids, texts, metadatas = [], [], []
    for doc in all_docs:
        title = doc.metadata.get("title", "untitled")
        url = doc.metadata.get("source", "")
        page_id = doc.metadata.get("id", title)
        for i, chunk in enumerate(chunk_text(doc.page_content)):
            ids.append(f"confluence-{page_id}-{i}")
            texts.append(f"{title}\n\n{chunk}")
            metadatas.append({"source": "confluence", "title": title, "url": url})

    print(f"Embedding {len(texts)} chunk(s) from {len(all_docs)} page(s)...")
    embeddings = []
    for i, text in enumerate(texts, start=1):
        embeddings.append(embedder.embed_text(text))
        if i % 10 == 0 or i == len(texts):
            print(f"  embedded {i}/{len(texts)}")

    store.add_records(ids, embeddings, texts, metadatas)
    print(f"Done. Chroma collection now has {store.count()} record(s) total.")


if __name__ == "__main__":
    main()
