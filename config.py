from __future__ import annotations

import ipaddress
import os
import socket
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
    "ZHIJIAO_CUSTOM_PROVIDER",
    "ZHIJIAO_ALLOW_PRIVATE_AI_ENDPOINTS",
    "DASHSCOPE_API_KEY",
    "ZHIJIAO_AI_PROVIDER",
    "ZHIJIAO_AI_BASE_URL",
    "ZHIJIAO_AI_MODEL",
    "ZHIJIAO_AI_READ_TIMEOUT",
    "ZHIJIAO_STUDENT_DEFAULT_PASSWORD",
    "ZHIJIAO_MINERU_URL",
    "ZHIJIAO_MINERU_TOKEN",
    "ZHIJIAO_MINERU_VERIFY_TLS",
    "ZHIJIAO_MINERU_TIMEOUT",
    "ZHIJIAO_MINERU_BACKEND",
    "ZHIJIAO_MINERU_LANG",
    "ZHIJIAO_FORMULA_URL",
    "ZHIJIAO_FORMULA_TOKEN",
    "ZHIJIAO_FORMULA_VERIFY_TLS",
    "ZHIJIAO_FORMULA_TIMEOUT",
    "ZHIJIAO_KNOWLEDGE_EXTRACTOR",
    "ZHIJIAO_DOCLING_GRAPH_CONTRACT",
    "ZHIJIAO_DOCLING_GRAPH_CHUNK_TOKENS",
    "ZHIJIAO_DOCLING_GRAPH_PARALLEL_WORKERS",
    "ZHIJIAO_DOCLING_GRAPH_CONTEXT_LIMIT",
    "ZHIJIAO_DOCLING_GRAPH_MAX_OUTPUT_TOKENS",
    "ZHIJIAO_INGESTION_BATCH_SIZE",
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


def get_runtime_setting(name: str, default: str = "") -> str:
    """Resolve a deployment setting from the process or the uncommitted server.env."""
    return os.environ.get(name, "").strip() or _read_env_file(SERVER_ENV).get(name, default).strip()


def get_ai_settings() -> dict[str, str | bool]:
    """Resolve AI settings dynamically so the web client can switch modes without restarting."""
    bundled_values = _read_env_file(BUNDLED_RELAY_ENV)
    server_values = _read_env_file(SERVER_ENV)
    user_values = _read_env_file(USER_AI_ENV)
    values: dict[str, str] = {}
    # No configuration intentionally falls back to deterministic Mock. A bundled
    # relay, local selection, server.env, or process environment may override it.
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
            mode = "mock"

    if mode == "mock":
        provider = "mock"
        base_url = ""
        api_key = ""
        model = "mock-course-assistant"
    elif mode == "custom":
        provider = values.get("ZHIJIAO_CUSTOM_PROVIDER", "auto").lower()
        base_url = values.get("ZHIJIAO_CUSTOM_BASE_URL", "")
        if provider == "auto":
            provider = "gemini" if "generativelanguage.googleapis.com" in base_url.lower() else "openai_compatible"
        api_key = values.get("ZHIJIAO_CUSTOM_API_KEY", "")
        model = values.get("ZHIJIAO_CUSTOM_MODEL", "")
    elif mode == "qwen":
        provider = values.get("ZHIJIAO_AI_PROVIDER", "qwen").lower()
        base_url = values.get(
            "ZHIJIAO_AI_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        api_key = values.get("DASHSCOPE_API_KEY", "")
        model = values.get("ZHIJIAO_AI_MODEL", "qwen-plus")
    elif mode == "relay":
        mode = "relay"
        provider = "relay"
        base_url = values.get("ZHIJIAO_RELAY_URL", "")
        api_key = values.get("ZHIJIAO_RELAY_TOKEN", "")
        model = "server-managed"
    else:
        mode = "mock"
        provider = "mock"
        base_url = ""
        api_key = ""
        model = "mock-course-assistant"

    # Be forgiving when a URL was copied from a quoted command or web page.
    base_url = base_url.strip().strip("\"'").rstrip("/")
    return {
        "mode": mode,
        "provider": provider,
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "configured": provider == "mock" or bool(base_url and model and (api_key or provider == "ollama")),
        "read_timeout": _positive_int_setting("ZHIJIAO_AI_READ_TIMEOUT", 115),
    }


def get_student_default_password() -> str:
    """Read the deployment-only initial password without persisting it in SQLite."""
    return (
        os.environ.get("ZHIJIAO_STUDENT_DEFAULT_PASSWORD", "").strip()
        or _read_env_file(SERVER_ENV).get("ZHIJIAO_STUDENT_DEFAULT_PASSWORD", "").strip()
    )


def _positive_int_setting(name: str, default: int) -> int:
    try:
        return max(1, int(get_runtime_setting(name, str(default))))
    except (TypeError, ValueError):
        return default


def get_knowledge_extractor_settings() -> dict[str, str | int | bool]:
    """Resolve the teacher knowledge-tree backend without local ML runtimes."""
    configured_backend = get_runtime_setting("ZHIJIAO_KNOWLEDGE_EXTRACTOR", "builtin").lower()
    deprecated_backend = configured_backend == "docling_graph"
    backend = "builtin"
    contract = get_runtime_setting("ZHIJIAO_DOCLING_GRAPH_CONTRACT", "auto").lower()
    if contract not in {"auto", "direct", "dense"}:
        contract = "auto"
    return {
        "backend": backend,
        "configured_backend": configured_backend,
        "deprecated_backend": deprecated_backend,
        "warning": (
            "docling_graph 已弃用；系统已改用不依赖 Docling/Torch 的原生证据知识树后端"
            if deprecated_backend else ""
        ),
        "contract": contract,
        "chunk_max_tokens": _positive_int_setting("ZHIJIAO_DOCLING_GRAPH_CHUNK_TOKENS", 768),
        "parallel_workers": _positive_int_setting("ZHIJIAO_DOCLING_GRAPH_PARALLEL_WORKERS", 1),
        "context_limit": _positive_int_setting("ZHIJIAO_DOCLING_GRAPH_CONTEXT_LIMIT", 128000),
        "max_output_tokens": _positive_int_setting("ZHIJIAO_DOCLING_GRAPH_MAX_OUTPUT_TOKENS", 8192),
    }


def get_document_ingestion_settings() -> dict[str, int]:
    """Runtime settings for the local, resumable document ingestion pipeline."""
    return {
        "batch_size": _positive_int_setting("ZHIJIAO_INGESTION_BATCH_SIZE", 40),
    }


def student_import_config_status() -> dict[str, str | bool | int]:
    environment_value = os.environ.get("ZHIJIAO_STUDENT_DEFAULT_PASSWORD", "").strip()
    file_value = _read_env_file(SERVER_ENV).get("ZHIJIAO_STUDENT_DEFAULT_PASSWORD", "").strip()
    value = environment_value or file_value
    length = len(value)
    return {
        "configured": bool(value),
        "source": "environment" if environment_value else ("server.env" if file_value else "not_configured"),
        "password_length": length,
        "security_level": "not_configured" if not value else ("weak" if length < 6 else ("medium" if length < 10 else "strong")),
        "config_path": str(SERVER_ENV),
    }


def save_user_ai_settings(
    mode: str,
    *,
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    provider: str = "auto",
) -> None:
    """Save an optional per-computer override. This file is excluded from Git."""
    if mode not in {"mock", "relay", "custom"}:
        raise ValueError("AI 配置模式无效")
    if any("\r" in value or "\n" in value for value in (base_url, api_key, model)):
        raise ValueError("AI 配置不能包含换行符")
    lines = [f"ZHIJIAO_AI_MODE={mode}"]
    if mode == "custom":
        clean_base_url = base_url.strip().strip("\"'").rstrip("/")
        provider = provider.strip().lower()
        if provider not in {"auto", "openai_compatible", "gemini", "ollama"}:
            raise ValueError("自定义接口类型无效")
        validate_ai_base_url(clean_base_url, allow_private=provider == "ollama")
        if (provider != "ollama" and not api_key.strip()) or not model.strip():
            raise ValueError("自定义模型名称不能为空，非 Ollama 接口还必须填写 API Key")
        lines.extend([
            f"ZHIJIAO_CUSTOM_PROVIDER={provider}",
            f"ZHIJIAO_CUSTOM_BASE_URL={clean_base_url}",
            f"ZHIJIAO_CUSTOM_API_KEY={api_key.strip()}",
            f"ZHIJIAO_CUSTOM_MODEL={model.strip()}",
        ])
    USER_AI_ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        USER_AI_ENV.chmod(0o600)
    except OSError:
        pass


def get_public_ai_settings() -> dict[str, str | bool]:
    """Return runtime AI settings without ever exposing a stored credential."""
    settings = get_ai_settings()
    return {
        "mode": str(settings["mode"]),
        "provider": str(settings["provider"]),
        "base_url": str(settings["base_url"]),
        "model": str(settings["model"]),
        "configured": bool(settings["configured"]),
        "has_api_key": bool(settings["api_key"]),
    }


def validate_ai_base_url(base_url: str, *, allow_private: bool = False) -> None:
    """Reject credentials and internal-network destinations unless explicitly allowed.

    This is a local deployment guard, not a complete network egress sandbox. Production
    deployments should also restrict outbound traffic at the host or container layer.
    """
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Base URL 必须是有效的 http:// 或 https:// 地址")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Base URL 不能包含账号、密码或片段标识")
    private_allowed = allow_private or get_runtime_setting(
        "ZHIJIAO_ALLOW_PRIVATE_AI_ENDPOINTS", "0"
    ).lower() in {"1", "true", "yes"}
    if private_allowed:
        return
    if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
        raise ValueError("自定义接口不能指向本机或内网地址")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
    except OSError:
        addresses = {parsed.hostname}
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError:
            continue
        # Clash and similar system proxies intentionally return benchmark-range
        # Fake-IP addresses while preserving the original public hostname for
        # the actual proxied request. Treat that documented range as a proxy
        # transport detail; literal private/loopback destinations remain blocked.
        if ip in ipaddress.ip_network("198.18.0.0/15"):
            continue
        if not ip.is_global:
            raise ValueError("自定义接口不能指向本机、内网或链路本地地址")


MATERIALS_DIR = BASE_DIR / "course_materials"
DATA_DIR = Path(os.environ.get("ZHIJIAO_DATA_DIR", BASE_DIR / "data")).resolve()
DB_PATH = DATA_DIR / "learning.db"
MAX_UPLOAD_MB = max(1, int(os.environ.get("ZHIJIAO_MAX_UPLOAD_MB", "500")))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

TOP_K = 4
MIN_EVIDENCE_SCORE = 0.12
MAX_EVIDENCE_CHARS = 800
TEACHER_PORTAL_ENABLED = get_runtime_setting("ZHIJIAO_TEACHER_AGENT_ENABLED", "0").lower() in {"1", "true", "yes"}

# Compatibility constants for existing imports. Provider construction reads the
# dynamic settings above on every call.
_INITIAL_AI = get_ai_settings()
AI_PROVIDER = str(_INITIAL_AI["provider"])
AI_API_KEY = str(_INITIAL_AI["api_key"])
AI_BASE_URL = str(_INITIAL_AI["base_url"])
AI_MODEL = str(_INITIAL_AI["model"])
