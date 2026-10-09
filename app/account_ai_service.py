from __future__ import annotations

from typing import Any

from campus_service import PermissionDenied, ValidationError
from config import get_ai_settings, validate_ai_base_url
from database import LearningDatabase
from job_secret_store import decrypt_job_secret, encrypt_job_secret


class AccountAiService:
    """Account preferences; credentials never overwrite the deployment default."""

    def __init__(self, db: LearningDatabase):
        self.db = db

    def _require_actor(self, actor: dict[str, Any]) -> str:
        user_id = str(actor.get("user_id") or "")
        user = self.db.fetch_one("SELECT role,status FROM users WHERE user_id=?", (user_id,))
        if not user or user["status"] != "active" or user["role"] != actor.get("role"):
            raise PermissionDenied("无权管理该账号的 AI 设置")
        return user_id

    def resolve(self, user_id: str) -> dict:
        row = self.db.fetch_one("SELECT * FROM account_ai_settings WHERE user_id=?", (user_id,))
        if not row:
            return get_ai_settings(use_request=False)
        if row["mode"] != "custom":
            return get_ai_settings(use_request=False, mode_override=row["mode"])
        return {
            "mode": "custom", "provider": row["provider"], "base_url": row["base_url"],
            "model": row["model"], "api_key": decrypt_job_secret(row["api_key_encrypted"]),
            "configured": True,
            "read_timeout": get_ai_settings(use_request=False).get("read_timeout", 115),
        }

    def public(self, actor: dict[str, Any]) -> dict:
        settings = self.resolve(self._require_actor(actor))
        return {**{key: settings[key] for key in ("mode", "provider", "base_url", "model", "configured")},
                "has_api_key": bool(settings.get("api_key")), "scope": "account"}

    def save(self, actor: dict[str, Any], *, mode: str, provider: str = "auto",
             base_url: str = "", model: str = "", api_key: str = "") -> dict:
        user_id = self._require_actor(actor)
        if mode not in {"mock", "relay", "custom"}:
            raise ValidationError("AI 配置模式无效")
        if any("\n" in value or "\r" in value for value in (provider, base_url, model, api_key)):
            raise ValidationError("AI 配置不能包含换行符")
        base_url = base_url.strip().strip("\"'").rstrip("/")
        provider, model, api_key = provider.strip().lower(), model.strip(), api_key.strip()
        encrypted = ""
        if mode == "custom":
            if provider == "auto":
                provider = "gemini" if "generativelanguage.googleapis.com" in base_url.lower() else "openai_compatible"
            if provider not in {"openai_compatible", "gemini", "ollama"}:
                raise ValidationError("自定义接口类型无效")
            try:
                validate_ai_base_url(base_url, allow_private=provider == "ollama")
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            if not model:
                raise ValidationError("模型名称不能为空")
            existing = self.db.fetch_one("SELECT * FROM account_ai_settings WHERE user_id=?", (user_id,))
            same_endpoint = existing and existing["provider"] == provider and existing["base_url"] == base_url
            if provider != "ollama":
                encrypted = encrypt_job_secret(api_key) if api_key else (
                    existing["api_key_encrypted"] if same_endpoint else ""
                )
                if not encrypted:
                    raise ValidationError("首次配置或更换接口地址时，请填写该接口的 API Key")
        else:
            provider = base_url = model = ""
        self.db.execute(
            """INSERT INTO account_ai_settings(user_id,mode,provider,base_url,model,api_key_encrypted)
               VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode,
               provider=excluded.provider,base_url=excluded.base_url,model=excluded.model,
               api_key_encrypted=excluded.api_key_encrypted,updated_at=CURRENT_TIMESTAMP""",
            (user_id, mode, provider, base_url, model, encrypted),
        )
        return self.public(actor)
