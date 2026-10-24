"""
Every tunable setting for this project, loaded from .env, in one place.

Nothing in this file has real logic - it's just named constants, so every
other file reads cleanly (e.g. `config.CHROMA_PERSIST_DIR` instead of
`os.environ["CHROMA_PERSIST_DIR"]` scattered across ten files). If you need
to change a model name, a URL, or a chunk size, this is the one file to
open - you should never need to touch the provider scripts themselves just
to change a parameter.

Setup: copy .env.example to .env and fill in your real values. .env is
gitignored; this file is not (it contains no secrets, just the loading
logic and defaults).
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


def _require(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required .env value: {name}\n"
            f"Copy .env.example to .env in this folder and fill it in."
        )
    return value


# ---------------------------------------------------------------------------
# LLM (Daisy) - required: almost everything in this project calls the LLM
# or the embeddings endpoint, so fail fast and clearly if these are missing
# instead of letting a cryptic error surface three files deep.
# ---------------------------------------------------------------------------
DAISY_BASE_URL = _require("DAISY_BASE_URL")
DAISY_API_KEY = _require("DAISY_API_KEY")
DAISY_CHAT_MODEL = os.environ.get("DAISY_CHAT_MODEL", "daisy-chat-default")

# ---------------------------------------------------------------------------
# Embeddings (Daisy) - defaults to the same base URL/key as chat, since
# that's the common case; override only if your org splits them.
# ---------------------------------------------------------------------------
DAISY_EMBEDDING_BASE_URL = os.environ.get("DAISY_EMBEDDING_BASE_URL", DAISY_BASE_URL)
DAISY_EMBEDDING_API_KEY = os.environ.get("DAISY_EMBEDDING_API_KEY", DAISY_API_KEY)
DAISY_EMBEDDING_MODEL = os.environ.get("DAISY_EMBEDDING_MODEL", "daisy-embedding-default")

# ---------------------------------------------------------------------------
# Vector store (ChromaDB) - local persistent client, no server needed.
# ---------------------------------------------------------------------------
CHROMA_PERSIST_DIR = os.environ.get("CHROMA_PERSIST_DIR", "./chroma_db")
CHROMA_COLLECTION = os.environ.get("CHROMA_COLLECTION", "incident_knowledge")

# ---------------------------------------------------------------------------
# ServiceNow - soft-optional: not required just to import config, since you
# may want to test retrieval before ServiceNow is wired up. servicenow_client.py
# will raise its own clear error if you call it without these set.
# ---------------------------------------------------------------------------
SN_INSTANCE = os.environ.get("SN_INSTANCE", "")
SN_AUTH_MODE = os.environ.get("SN_AUTH_MODE", "basic")  # "basic" or "oauth_password"
SN_USER = os.environ.get("SN_USER", "")
SN_PASS = os.environ.get("SN_PASS", "")
SN_CLIENT_ID = os.environ.get("SN_CLIENT_ID", "")
SN_CLIENT_SECRET = os.environ.get("SN_CLIENT_SECRET", "")

# ---------------------------------------------------------------------------
# Confluence - same soft-optional reasoning as ServiceNow above.
# ---------------------------------------------------------------------------
CONFLUENCE_URL = os.environ.get("CONFLUENCE_URL", "")
CONFLUENCE_PAT = os.environ.get("CONFLUENCE_PAT", "")
CONFLUENCE_USERNAME = os.environ.get("CONFLUENCE_USERNAME", "")
CONFLUENCE_CLOUD = os.environ.get("CONFLUENCE_CLOUD", "false").strip().lower() == "true"
CONFLUENCE_SPACE_KEYS = os.environ.get("CONFLUENCE_SPACE_KEYS", "")

# ---------------------------------------------------------------------------
# Retrieval tuning
# ---------------------------------------------------------------------------
TOP_K = int(os.environ.get("TOP_K", "3"))
CHUNK_SIZE_CHARS = int(os.environ.get("CHUNK_SIZE_CHARS", "1500"))
CHUNK_OVERLAP_CHARS = int(os.environ.get("CHUNK_OVERLAP_CHARS", "200"))
