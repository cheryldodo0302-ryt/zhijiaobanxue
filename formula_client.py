from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from config import get_runtime_setting


class Pix2TextClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or get_runtime_setting("ZHIJIAO_FORMULA_URL")).rstrip("/")
        self.token = get_runtime_setting("ZHIJIAO_FORMULA_TOKEN")
        self.verify_tls = get_runtime_setting("ZHIJIAO_FORMULA_VERIFY_TLS", "1").lower() not in {
            "0", "false", "no", "off",
        }
        self.session = requests.Session()
        if urlparse(self.base_url).hostname in {"127.0.0.1", "localhost", "::1"}:
            self.session.trust_env = False

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def recognize(self, image_path: Path) -> dict[str, Any]:
        with image_path.open("rb") as stream:
            response = self.session.post(
                f"{self.base_url}/v1/formula",
                files={"file": (image_path.name, stream, "image/jpeg")},
                headers=self.headers,
                timeout=(15, int(get_runtime_setting("ZHIJIAO_FORMULA_TIMEOUT", "600"))),
                verify=self.verify_tls,
            )
        response.raise_for_status()
        return response.json()

    def health(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "status": "disabled"}
        response = self.session.get(
            f"{self.base_url}/health", headers=self.headers, timeout=10, verify=self.verify_tls,
        )
        response.raise_for_status()
        payload = response.json()
        return {"enabled": True, "status": "ok", **payload}
