import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_server_environment() -> None:
    """Load deployment secrets from a backend-only file before reading settings."""
    server_env = BASE_DIR / "server.env"
    if not server_env.exists():
        return
    try:
        lines = server_env.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name in {"DASHSCOPE_API_KEY", "ZHIJIAO_AI_PROVIDER", "ZHIJIAO_AI_BASE_URL", "ZHIJIAO_AI_MODEL"}:
            os.environ[name] = value.strip()


_load_server_environment()

MATERIALS_DIR = BASE_DIR / "course_materials"
DATA_DIR = Path(os.environ.get("ZHIJIAO_DATA_DIR", BASE_DIR / "data")).resolve()
DB_PATH = DATA_DIR / "learning.db"

TOP_K = 4
MIN_EVIDENCE_SCORE = 0.12
MAX_EVIDENCE_CHARS = 800
TEACHER_PORTAL_ENABLED = False

# AI settings are server-side only and are never collected in the browser.
# Production defaults target Alibaba Cloud Model Studio's real Qwen endpoint.
AI_PROVIDER = os.environ.get("ZHIJIAO_AI_PROVIDER", "qwen").strip().lower()
AI_API_KEY = os.environ.get("DASHSCOPE_API_KEY", os.environ.get("ZHIJIAO_AI_API_KEY", "")).strip()
AI_BASE_URL = os.environ.get(
    "ZHIJIAO_AI_BASE_URL",
    "https://ws-c4qflt1k6x8xwd4f.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
).strip()
AI_MODEL = os.environ.get("ZHIJIAO_AI_MODEL", "qwen-plus").strip()
