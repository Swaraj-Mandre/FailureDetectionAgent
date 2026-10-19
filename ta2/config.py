"""Settings shared by every PBA 2 module. Change things here, not in the code."""
from pathlib import Path

TA2_DIR = Path(__file__).resolve().parent
REPO_DIR = TA2_DIR.parent

SHOP_DB = REPO_DIR / "data" / "shop.db"
CORPUS_FILE = TA2_DIR / "corpus" / "error_guide.md"

# Everything the agent writes at run time lives in ta2/state/ (ignored by git).
STATE_DIR = TA2_DIR / "state"
CHECKPOINT_DB = STATE_DIR / "checkpoints.db"   # short-term memory, resume after a crash
STORE_DB = STATE_DIR / "store.db"              # long-term memory (business glossary)
FAILURE_LOG_DB = STATE_DIR / "failure_log.db"  # every failure the agents saw
CHROMA_DIR = STATE_DIR / "chroma_db"           # vector index of the error guide

TRACE_DIR = REPO_DIR / "traces" / "ta2"

CHAT_MODEL = "qwen2.5:3b"
EMBED_MODEL = "nomic-embed-text"

RETRY_BUDGET = 3   # recovery attempts per question before handing over to a human
RAG_K = 3          # error-guide sections retrieved for each piece of advice
DEFAULT_USER = "owner_pune"   # whose glossary to use when none is given

STATE_DIR.mkdir(parents=True, exist_ok=True)
