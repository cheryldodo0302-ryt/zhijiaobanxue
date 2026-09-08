from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
MATERIALS_DIR = BASE_DIR / "course_materials"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "learning.db"

TOP_K = 4
MIN_EVIDENCE_SCORE = 0.12
MAX_EVIDENCE_CHARS = 800

