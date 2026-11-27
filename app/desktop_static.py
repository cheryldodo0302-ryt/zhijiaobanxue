"""History-route fallback for the packaged Vue interface."""
from pathlib import Path

from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException


class VueFiles(StaticFiles):
    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            normalized = path.replace("\\", "/").lstrip("/")
            first = normalized.split("/", 1)[0]
            if exc.status_code != 404 or first in {"api", "assets", "models", "mediapipe"} or Path(normalized).suffix:
                raise
            return await super().get_response("index.html", scope)
