"""Read the same saved model and encrypted credential used by knowledge analysis."""
from contextlib import closing
from pathlib import Path
import sqlite3

from job_secret_store import decrypt_job_secret


class SharedAiSettingsError(ValueError):
    pass


def load_shared_ai_settings(user_id: str, database: str | Path) -> dict:
    path = Path(database).resolve()
    if not path.is_file():
        raise SharedAiSettingsError("找不到智教伴学数据库，请先启动系统。")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        actor = conn.execute(
            "SELECT role,status,must_change_password FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        if (not actor or actor["status"] != "active" or actor["must_change_password"]
                or actor["role"] not in {"teacher", "student"}):
            raise SharedAiSettingsError("绑定账号不可用，请检查账号状态及初始密码。")
        if actor["role"] == "teacher":
            row = conn.execute("SELECT * FROM teacher_ai_settings WHERE teacher_id=?", (user_id,)).fetchone()
            source = "knowledge_api"
        else:
            row = conn.execute("SELECT * FROM account_ai_settings WHERE user_id=?", (user_id,)).fetchone()
            if row and row["mode"] != "custom":
                row = None
            source = "account_api"
        if not row:
            raise SharedAiSettingsError("请先在知识库的 API 分析设置中保存接口地址、模型和 API Key。")
        provider, base_url, model = row["provider"], row["base_url"].rstrip("/"), row["model"]
        if base_url.endswith('/chat/completions'):
            base_url = base_url.removesuffix('/chat/completions')
        if provider not in {"openai_compatible", "gemini", "ollama"} or not base_url or not model:
            raise SharedAiSettingsError("知识库保存的 API 配置不完整或协议不受支持。")
        key = decrypt_job_secret(row["api_key_encrypted"], key_path=path.parent / ".job-secret.key")
        if not key and provider != "ollama":
            raise SharedAiSettingsError("知识库保存的 API Key 不可用，请在网页端重新保存。")
        return {"source": source, "provider": provider, "base_url": base_url,
                "model": model, "api_key": key}


def build_shared_model_patch(settings: dict, *, state_dir: Path,
                             bridge_url: str, bridge_token: str) -> dict:
    provider_id = "zhijiao-knowledge"
    ref = provider_id + "/" + settings["model"]
    provider = {
        "baseUrl": bridge_url,
        "api": "google-generative-ai" if settings["provider"] == "gemini" else "openai-completions",
        "agentRuntime": {"id": "openclaw"},
        "models": [{"id": settings["model"], "name": settings["model"],
                    "reasoning": False, "input": ["text"], "contextWindow": 65536,
                    "responseModelEquivalent": True,
                    "maxTokens": 4096,
                    "compat": {"supportsDeveloperRole": False, "supportsStore": False,
                               "supportsReasoningEffort": False, "maxTokensField": "max_tokens"}}],
    }
    # This token authenticates only the loopback bridge. The upstream model's
    # API key is read from the encrypted database on every request.
    provider["apiKey"] = bridge_token
    return {
        "env": {"vars": {"NO_PROXY": "localhost,127.0.0.1,::1"}},
        "models": {"providers": {provider_id: provider}},
        "agents": {"defaults": {
            "workspace": str(state_dir / "workspace"), "skipBootstrap": True,
            "model": {"primary": ref, "fallbacks": []},
            "utilityModel": {"primary": ref, "fallbacks": []}, "thinkingDefault": "off",
        }},
        "tools": {"allow": ["bundle-mcp"]},
    }
