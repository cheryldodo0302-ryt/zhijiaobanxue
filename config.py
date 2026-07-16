from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent
USER_AI_ENV = BASE_DIR / "user_ai.env"
BUNDLED_RELAY_ENV = BASE_DIR / "relay_client.env"
SERVER_ENV = BASE_DIR / "server.env"

_AI_NAMES = {
    "ZHIJIAO_AI_MODE",
    "ZHIJIAO_RELAY_URL",
    "ZHIJIAO_RELAY_TOKEN",
    "ZHIJIAO_CUSTOM_BASE_URL",
    "ZHIJIAO_CUSTOM_API_KEY",
    "ZHIJIAO_CUSTOM_MODEL",
    "DASHSCOPE_API_KEY",
    "ZHIJIAO_AI_PROVIDER",
    "ZHIJIAO_AI_BASE_URL",
    "ZHIJIAO_AI_MODEL",
}


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() in _AI_NAMES:
            values[name.strip()] = value.strip()
    return values


def get_ai_settings() -> dict[str, str | bool]:
    """Resolve AI settings dynamically so Streamlit can switch modes without restarting."""
    bundled_values = _read_env_file(BUNDLED_RELAY_ENV)
    server_values = _read_env_file(SERVER_ENV)
    user_values = _read_env_file(USER_AI_ENV)
    values: dict[str, str] = {}
    # A deployed relay is the safe default. Legacy server.env and the user's local
    # selection may override it; process environment variables have final priority.
    for source in (bundled_values, server_values, user_values):
        values.update(source)
    for name in _AI_NAMES:
        if name in os.environ:
            values[name] = os.environ[name].strip()

    mode = os.environ.get("ZHIJIAO_AI_MODE", "").strip().lower()
    if not mode:
        mode = user_values.get("ZHIJIAO_AI_MODE", "").lower()
    if not mode and (
        os.environ.get("DASHSCOPE_API_KEY") or server_values.get("DASHSCOPE_API_KEY")
    ):
        mode = "qwen"
    if not mode:
        mode = bundled_values.get("ZHIJIAO_AI_MODE", "").lower()
    if not mode:
        if values.get("ZHIJIAO_RELAY_URL"):
            mode = "relay"
        elif values.get("DASHSCOPE_API_KEY"):
            mode = "qwen"
        else:
            mode = "relay"

    if mode == "custom":
        provider = "openai_compatible"
        base_url = values.get("ZHIJIAO_CUSTOM_BASE_URL", "")
        api_key = values.get("ZHIJIAO_CUSTOM_API_KEY", "")
        model = values.get("ZHIJIAO_CUSTOM_MODEL", "")
    elif mode == "qwen":
        provider = values.get("ZHIJIAO_AI_PROVIDER", "qwen").lower()
        base_url = values.get(
            "ZHIJIAO_AI_BASE_URL",
            "https://ws-c4qflt1k6x8xwd4f.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        )
        api_key = values.get("DASHSCOPE_API_KEY", "")
        model = values.get("ZHIJIAO_AI_MODEL", "qwen-plus")
    else:
        mode = "relay"
        provider = "relay"
        base_url = values.get("ZHIJIAO_RELAY_URL", "")
        api_key = values.get("ZHIJIAO_RELAY_TOKEN", "")
        model = "server-managed"

    base_url = base_url.rstrip("/")
    return {
        "mode": mode,
        "provider": provider,
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "configured": bool(base_url and api_key and model),
    }


def save_user_ai_settings(
    mode: str,
    *,
    base_url: str = "",
    api_key: str = "",
    model: str = "",
) -> None:
    """Save an optional per-computer override. This file is excluded from Git."""
    if mode not in {"relay", "custom"}:
        raise ValueError("AI 配置模式无效")
    if any("\r" in value or "\n" in value for value in (base_url, api_key, model)):
        raise ValueError("AI 配置不能包含换行符")
    lines = [f"ZHIJIAO_AI_MODE={mode}"]
    if mode == "custom":
        parsed = urlparse(base_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Base URL 必须是有效的 http:// 或 https:// 地址")
        if not api_key.strip() or not model.strip():
            raise ValueError("自定义 API Key 和模型名称不能为空")
        lines.extend([
            f"ZHIJIAO_CUSTOM_BASE_URL={base_url.strip().rstrip('/')}",
            f"ZHIJIAO_CUSTOM_API_KEY={api_key.strip()}",
            f"ZHIJIAO_CUSTOM_MODEL={model.strip()}",
        ])
    USER_AI_ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        USER_AI_ENV.chmod(0o600)
    except OSError:
        pass


MATERIALS_DIR = BASE_DIR / "course_materials"
DATA_DIR = Path(os.environ.get("ZHIJIAO_DATA_DIR", BASE_DIR / "data")).resolve()
DB_PATH = DATA_DIR / "learning.db"

TOP_K = 4
MIN_EVIDENCE_SCORE = 0.12
MAX_EVIDENCE_CHARS = 800
TEACHER_PORTAL_ENABLED = False

# Compatibility constants for existing imports. Provider construction reads the
# dynamic settings above on every call.
_INITIAL_AI = get_ai_settings()
AI_PROVIDER = str(_INITIAL_AI["provider"])
AI_API_KEY = str(_INITIAL_AI["api_key"])
AI_BASE_URL = str(_INITIAL_AI["base_url"])
AI_MODEL = str(_INITIAL_AI["model"])
