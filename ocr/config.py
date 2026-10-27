"""Settings. Everything comes from environment variables so nothing secret lives in the code."""
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("OCR_DATA", APP_DIR / "data")).resolve()
JOBS = DATA / "jobs"

MAX_MB = int(os.environ.get("MAX_UPLOAD_MB", 50))
MAX_PAGES = int(os.environ.get("MAX_PAGES", 20))
USER_DAILY_PAGES = int(os.environ.get("USER_DAILY_PAGES", 100))   # pages one person may upload in 24 hours; 0 turns the limit off
JOB_TIMEOUT = int(os.environ.get("JOB_TIMEOUT", 3600))      # pipeline run, seconds
OP_TIMEOUT = int(os.environ.get("OP_TIMEOUT", 600))         # rebuild / review, seconds

# sign-in
AUTH_DISABLED = os.environ.get("AUTH_DISABLED") == "1"       # local development only
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
ALLOWED_EMAILS = {e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()}
ALLOWED_DOMAIN = os.environ.get("ALLOWED_DOMAIN", "").strip().lower()
SESSION_HOURS = int(os.environ.get("SESSION_HOURS", 24 * 7))

# screen
DISPLAY_DPI = 110      # page images shown in the browser (the pipeline itself works at the notebook's DPI)
CROP_PAD = 0.01        # share of the page added around a block when it is cropped for re-reading
