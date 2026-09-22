from __future__ import annotations

import io
import os
import secrets
import logging
from functools import lru_cache

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
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
async def recognize_formula(file: UploadFile = File(...), authorization: str = Header(default='')) -> dict:
    token = os.environ.get('ZHIJIAO_FORMULA_TOKEN','').strip()
    if not token:
        raise HTTPException(status_code=503,detail='公式服务尚未配置访问令牌')
    if not secrets.compare_digest(authorization, 'Bearer '+token):
        raise HTTPException(status_code=401,detail='公式服务访问令牌无效')
    limit = 10 * 1024 * 1024
    data = await file.read(limit+1)
    if not data or len(data)>limit:
        raise HTTPException(status_code=413,detail='图片不能为空或超过 10MB')
    try:
        image = Image.open(io.BytesIO(data))
        if image.width * image.height > 25_000_000:
            raise HTTPException(status_code=413,detail='图片像素数量超过限制')
        image = image.convert('RGB')
        result = engine().recognize(image, file_type="formula")
        latex = result if isinstance(result, str) else str(result)
        return {"latex": latex.strip(), "engine": "pix2text", "model": "mfr"}
    except HTTPException:
        raise
    except Exception as exc:
        logging.getLogger(__name__).exception('Formula recognition failed')
        raise HTTPException(status_code=422, detail='无法识别该公式图片，请检查文件后重试') from exc
