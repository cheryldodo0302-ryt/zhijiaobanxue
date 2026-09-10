from __future__ import annotations

import io
import os
from functools import lru_cache

from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image

app = FastAPI(title="Zhijiao Pix2Text Formula Worker", version="1.0")


@lru_cache(maxsize=1)
def engine():
    from pix2text import Pix2Text
    return Pix2Text.from_config(enable_formula=True, enable_table=False,
                                device=os.environ.get("PIX2TEXT_DEVICE", "cuda"))


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "engine": "pix2text", "loaded": engine.cache_info().currsize > 0}


@app.post("/v1/formula")
async def recognize_formula(file: UploadFile = File(...)) -> dict:
    try:
        image = Image.open(io.BytesIO(await file.read())).convert("RGB")
        result = engine().recognize(image, file_type="formula")
        latex = result if isinstance(result, str) else str(result)
        return {"latex": latex.strip(), "engine": "pix2text", "model": "mfr"}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
