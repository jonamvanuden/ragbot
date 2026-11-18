import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"
DATA_DIR = ROOT / "data"

# Switch without touching code:  LLM=deepseek-r1-14b uvicorn ...
#DEFAULT_MODEL = os.getenv("LLM", "mistral-small-3.2")
#DEFAULT_MODEL = os.getenv("LLM", "deepseek-r1-14b")
DEFAULT_MODEL = os.getenv("LLM", "qwen3.6-35b-a3b")

# ---------- knowledge base ----------
START_URL = "https://www.personal-planer.de/"
MAX_PAGES = 300                  # safety limit for the crawler
DOCS_DIR = DATA_DIR / "docs"     # internal documents
QDRANT_DIR = DATA_DIR / "qdrant"
ERP_DB = DATA_DIR / "erp.db"  
COLLECTION = "personal_planer"

EMBED_MODEL = "BAAI/bge-m3"      # multilingual, very good at German
EMBED_DEVICE = os.getenv("EMBED_DEVICE", "cuda")
CHUNK_SIZE = 1000                # characters per chunk
CHUNK_OVERLAP = 150
TOP_K = 5                        # chunks handed to the LLM
MIN_SCORE = 0.35                 # below this a chunk counts as "not relevant"

SYSTEM_PROMPT = (
    "Du bist der interne KI-Assistent von Personal-Planer, dem HR-Systemhaus. "
    "Antworte immer auf Deutsch, klar und präzise. "
    "Fragen zu Personal-Planer-Produkten beantwortest du ausschließlich anhand des "
    "mitgelieferten Kontexts und nennst die Quellen als [1], [2]. "
    "Steht etwas nicht im Kontext, sag ehrlich, dass du es nicht weißt. "
    "Erfinde niemals Funktionen, Preise oder Details. "
    "Bei Begrüßungen oder Smalltalk antwortest du ganz normal und kurz."
)