from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path
from typing import Any

import requests

from config import get_runtime_setting


class MinerUError(RuntimeError):
    pass


class MinerUClient:
    """Small adapter around the official mineru-api HTTP contract."""

    def __init__(self, base_url: str | None = None, timeout: int | None = None):
        self.base_url = (base_url or get_runtime_setting("ZHIJIAO_MINERU_URL")).rstrip("/")
        self.timeout = timeout or int(get_runtime_setting("ZHIJIAO_MINERU_TIMEOUT", "3600"))
        self.token = get_runtime_setting("ZHIJIAO_MINERU_TOKEN")
        self.verify_tls = get_runtime_setting("ZHIJIAO_MINERU_VERIFY_TLS", "1").lower() not in {
            "0", "false", "no", "off",
        }

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def health(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "status": "disabled"}
        response = requests.get(
            f"{self.base_url}/health", headers=self.headers, timeout=10, verify=self.verify_tls,
        )
        response.raise_for_status()
        payload = response.json()
        return {"enabled": True, "status": "ok", **payload}

    def parse(self, path: Path, *, method: str = "auto", asset_dir: Path | None = None) -> dict[str, Any]:
        if not self.enabled:
            raise MinerUError("MinerU worker is not configured")
        with path.open("rb") as stream:
            response = requests.post(
                f"{self.base_url}/file_parse",
                files={"files": (path.name, stream, "application/octet-stream")},
                data={
                    "backend": get_runtime_setting("ZHIJIAO_MINERU_BACKEND", "pipeline"),
                    "parse_method": method,
                    "lang_list": get_runtime_setting("ZHIJIAO_MINERU_LANG", "ch"),
                    "formula_enable": "true",
                    "table_enable": "true",
                    "return_md": "true",
                    "return_middle_json": "true",
                    "return_content_list": "true",
                    "return_images": "true",
                    "response_format_zip": "true",
                    "return_original_file": "false",
                },
                headers=self.headers,
                timeout=(30, self.timeout),
                verify=self.verify_tls,
            )
        if response.status_code >= 400:
            raise MinerUError(f"MinerU {response.status_code}: {response.text[:500]}")
        try:
            archive = zipfile.ZipFile(io.BytesIO(response.content))
        except zipfile.BadZipFile as exc:
            raise MinerUError("MinerU did not return a ZIP result") from exc
        names = archive.namelist()
        middle = next((name for name in names if name.endswith("_middle.json") or name.endswith("middle.json")), None)
        if not middle:
            raise MinerUError(f"MinerU result has no middle.json: {names[:20]}")
        payload = json.loads(archive.read(middle).decode("utf-8"))
        payload["_archive_files"] = names
        markdown_name = next(
            (name for name in names if name.lower().endswith(".md") and not name.endswith("/")),
            None,
        )
        payload["_markdown"] = (
            archive.read(markdown_name).decode("utf-8", errors="replace") if markdown_name else ""
        )
        image_paths: dict[str, str] = {}
        if asset_dir is not None:
            asset_dir.mkdir(parents=True, exist_ok=True)
            for name in names:
                if "/images/" not in name or name.endswith("/"):
                    continue
                filename = Path(name).name
                destination = (asset_dir / filename).resolve()
                if asset_dir.resolve() not in destination.parents:
                    continue
                destination.write_bytes(archive.read(name))
                image_paths[filename] = str(destination)
        payload["_image_paths"] = image_paths
        return payload
