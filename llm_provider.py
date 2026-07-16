import base64
import json
import re
import ssl
from abc import ABC, abstractmethod
from typing import Any

import certifi
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import get_ai_settings


class TLS12Adapter(HTTPAdapter):
    """Use TLS 1.2 for compatibility with restrictive campus network middleboxes."""

    def __init__(self, *args, **kwargs):
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())
        self.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.ssl_context.maximum_version = ssl.TLSVersion.TLSv1_2
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs["ssl_context"] = self.ssl_context
        return super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        proxy_kwargs["ssl_context"] = self.ssl_context
        return super().proxy_manager_for(proxy, **proxy_kwargs)


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Return plain text generated from the supplied prompts."""


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: str, model: str, timeout: int = 90):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.last_endpoint = ""
        self.session = requests.Session()
        self.session.trust_env = True
        retries = Retry(total=1, connect=1, read=1, backoff_factor=0.5,
                        status_forcelist=(429, 500, 502, 503, 504),
                        allowed_methods=frozenset({"POST"}))
        self.session.mount("https://", TLS12Adapter(max_retries=retries))

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.base_url:
            raise RuntimeError("尚未配置智能服务 Base URL")
        base_urls = [self.base_url]
        failures = []
        response = None
        for base_url in base_urls:
            endpoint = f"{base_url}/chat/completions"
            try:
                response = self.session.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "User-Agent": "ZhijiaoBanxue/1.0",
                    },
                    json=payload,
                    timeout=(10, self.timeout),
                    verify=certifi.where(),
                )
                self.last_endpoint = endpoint
                break
            except (requests.exceptions.SSLError, requests.exceptions.ProxyError,
                    requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                failures.append(f"{endpoint} -> {type(exc).__name__}: {exc}")
        if response is None:
            raise RuntimeError("无法连接智能服务，请检查网络或接口配置。详情：" + " | ".join(failures))
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(f"千问返回了非 JSON 内容（HTTP {response.status_code}）：{response.text[:300]}") from exc
        if response.status_code >= 400:
            message = data.get("message") or data.get("error", {}).get("message") or str(data)
            raise RuntimeError(f"千问 API 调用失败（HTTP {response.status_code}）：{message}")
        return data

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        data = self._post({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 3500,
        })
        try:
            content = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"千问 API 返回格式异常：{str(data)[:300]}") from exc
        if not content:
            raise RuntimeError("千问返回了空内容")
        return content

    def generate_json(self, system_prompt: str, user_prompt: str) -> Any:
        raw = self.generate(system_prompt, user_prompt)
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I | re.S)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"千问未返回有效 JSON：{raw[:300]}") from exc


class QwenProvider(OpenAICompatibleProvider):
    """Real Qwen provider through the workspace OpenAI-compatible endpoint."""

    def extract_image_text(self, image_bytes: bytes, mime_type: str,
                           ocr_model: str = "qwen-vl-ocr") -> str:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        data = self._post({
            "model": ocr_model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}},
                    {"type": "text", "text": "请完整提取图片中的文字，保持原有段落顺序，只输出提取结果。"},
                ],
            }],
            "temperature": 0,
            "max_tokens": 3000,
        })
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("千问 OCR 返回格式异常") from exc


def build_backend_provider() -> LLMProvider:
    settings = get_ai_settings()
    provider = str(settings["provider"])
    api_key = str(settings["api_key"])
    base_url = str(settings["base_url"])
    model = str(settings["model"])
    if not settings["configured"]:
        if provider == "relay":
            raise RuntimeError("默认云端智能服务尚未部署或客户端中转配置缺失")
        raise RuntimeError("自定义智能服务配置不完整")
    if provider in {"relay", "qwen", "dashscope", "aliyun"}:
        return QwenProvider(api_key, base_url, model)
    if provider in {"openai", "openai_compatible"}:
        # Use QwenProvider here because it also implements the OpenAI-compatible
        # multimodal OCR request used by the student material workflow.
        return QwenProvider(api_key, base_url, model)
    raise RuntimeError(f"后端智能体类型不受支持：{provider}")


def backend_provider_status() -> dict[str, str | bool]:
    settings = get_ai_settings()
    return {
        "mode": str(settings["mode"]),
        "provider": str(settings["provider"]),
        "model": str(settings["model"]) or "not_configured",
        "base_url": str(settings["base_url"]),
        "configured": bool(settings["configured"]),
    }
