import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[2]  # backend/
REPO_DIR = BASE_DIR.parent
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "leaseguard.db"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "uploads")))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Extraction - Groq (replaces Anthropic per project decision)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
EXTRACTION_MODEL = os.getenv("EXTRACTION_MODEL", "llama-3.3-70b-versatile")
USE_FIXTURES = os.getenv("USE_FIXTURES", "true").lower() in {"1", "true", "yes"}

ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:8000").split(",") if o.strip()]
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))
MAX_PAGES = int(os.getenv("MAX_PAGES", "50"))
DEMO_USER_ID = "demo-user"
DEMO_USER_EMAIL = "demo@leaseguard.local"
