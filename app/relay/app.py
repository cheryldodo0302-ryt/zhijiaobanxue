from __future__ import annotations

import hmac
import os
import ssl
import threading
import time
from collections import defaultdict, deque
from typing import Any

import certifi
import requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

QWEN_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "").strip()
RELAY_CLIENT_TOKEN = os.environ.get("ZHIJIAO_RELAY_CLIENT_TOKEN", "").strip()
QWEN_BASE_URL = os.environ.get(
    "ZHIJIAO_QWEN_BASE_URL",
    "https://ws-c4qflt1k6x8xwd4f.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
).rstrip("/")
QWEN_TEXT_MODEL = os.environ.get("ZHIJIAO_QWEN_TEXT_MODEL", "qwen-plus").strip()
QWEN_OCR_MODEL = os.environ.get("ZHIJIAO_QWEN_OCR_MODEL", "qwen-vl-ocr").strip()
MAX_BODY_BYTES = int(os.environ.get("ZHIJIAO_RELAY_MAX_BODY_BYTES", 18 * 1024 * 1024))
RATE_LIMIT_PER_MINUTE = int(os.environ.get("ZHIJIAO_RELAY_RATE_LIMIT", 30))
UPSTREAM_READ_TIMEOUT = int(os.environ.get("ZHIJIAO_RELAY_UPSTREAM_TIMEOUT", 105))

app = FastAPI(title="智教伴学云端中转", version="1.0.0")
session = requests.Session()
_request_times: dict[str, deque[float]] = defaultdict(deque)
_rate_lock = threading.Lock()


class TLS12Adapter(HTTPAdapter):
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


session.mount(
    "https://",
    TLS12Adapter(max_retries=Retry(
        total=1,
        connect=1,
        read=0,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"POST"}),
    )),
)


def require_configuration() -> None:
    if not QWEN_API_KEY or not RELAY_CLIENT_TOKEN:
        raise HTTPException(status_code=503, detail="relay_not_configured")


def authorize(authorization: str | None) -> None:
    require_configuration()
    expected = f"Bearer {RELAY_CLIENT_TOKEN}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid_relay_token")


def enforce_rate_limit(client_id: str) -> None:
    now = time.monotonic()
    with _rate_lock:
        requests_for_client = _request_times[client_id]
        while requests_for_client and requests_for_client[0] <= now - 60:
            requests_for_client.popleft()
        if len(requests_for_client) >= RATE_LIMIT_PER_MINUTE:
            raise HTTPException(status_code=429, detail="relay_rate_limit_exceeded")
        requests_for_client.append(now)


def contains_image(messages: list[dict[str, Any]]) -> bool:
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(
            isinstance(item, dict) and item.get("type") == "image_url" for item in content
        ):
            return True
    return False


@app.get("/health")
def health() -> dict[str, str]:
    require_configuration()
    return {"status": "ok"}


@app.post("/compatible-mode/v1/chat/completions")
async def chat_completions(
    request: Request,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    authorize(authorization)
    enforce_rate_limit(request.client.host if request.client else "unknown")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="request_too_large")
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid_json") from exc
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=422, detail="messages_required")
    upstream_payload = {
        "model": QWEN_OCR_MODEL if contains_image(messages) else QWEN_TEXT_MODEL,
        "messages": messages,
        "temperature": max(0, min(float(payload.get("temperature", 0.2)), 1)),
        "max_tokens": max(64, min(int(payload.get("max_tokens", 3500)), 6000)),
    }
    try:
        response = session.post(
            f"{QWEN_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {QWEN_API_KEY}",
                "Content-Type": "application/json",
            },
            json=upstream_payload,
            timeout=(10, UPSTREAM_READ_TIMEOUT),
            verify=certifi.where(),
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail="qwen_connection_failed") from exc
    try:
        content = response.json()
    except ValueError:
        content = {"error": {"message": "qwen_invalid_response"}}
    return JSONResponse(status_code=response.status_code, content=content)
