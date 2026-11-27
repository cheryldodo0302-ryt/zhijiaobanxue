"""Loopback model endpoint that always uses the saved knowledge API settings."""
from __future__ import annotations

import hmac
import json
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
import httpx

from openclaw_settings import load_shared_ai_settings


def create_model_bridge(config_path: Path, *, transport=None) -> FastAPI:
    app = FastAPI(title="智教伴学本机共享模型", docs_url=None, redoc_url=None, openapi_url=None)

    def binding() -> dict:
        return json.loads(config_path.read_text(encoding='utf-8'))

    def authorize(request: Request) -> dict:
        saved = binding()
        supplied = request.headers.get('authorization', '')
        google_key = request.headers.get('x-goog-api-key') or request.query_params.get('key', '')
        if not (hmac.compare_digest(supplied.encode(), ('Bearer ' + saved['token']).encode())
                or (google_key and hmac.compare_digest(google_key.encode(), saved['token'].encode()))):
            raise HTTPException(401, detail='invalid_local_model_token')
        return saved

    def settings(saved: dict) -> dict:
        try:
            return load_shared_ai_settings(saved['user_id'], saved['database'])
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(503, detail=str(exc)) from exc

    @app.get('/health')
    def health():
        return {'ok': True}

    @app.get('/v1/models')
    def models(request: Request):
        selected = settings(authorize(request))
        return {'object': 'list', 'data': [{'id': selected['model'], 'object': 'model',
                                          'owned_by': 'zhijiao-knowledge'}]}

    async def forward(request: Request, *, google_action: str | None = None):
        selected = settings(authorize(request))
        body = await request.body()
        if len(body) > 32 * 1024 * 1024:
            raise HTTPException(413, detail='model_request_too_large')
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise HTTPException(400, detail='invalid_json') from exc
        if not isinstance(payload, dict):
            raise HTTPException(400, detail='model_request_must_be_object')
        base = selected['base_url'].rstrip('/')
        headers = {'Content-Type': 'application/json'}
        if google_action:
            if selected['provider'] != 'gemini':
                raise HTTPException(409, detail='model_protocol_changed_restart_openclaw')
            model = selected['model'].removeprefix('models/')
            url = f'{base}/models/{model}:{google_action}'
            if google_action == 'streamGenerateContent':
                url += '?alt=sse'
            headers['x-goog-api-key'] = selected['api_key']
        else:
            if selected['provider'] == 'gemini':
                raise HTTPException(409, detail='model_protocol_changed_restart_openclaw')
            url = base + '/chat/completions'
            payload['model'] = selected['model']
            if selected['api_key']:
                headers['Authorization'] = 'Bearer ' + selected['api_key']
        # Regional Alibaba endpoints work directly; other custom endpoints keep
        # the same proxy environment the application normally uses.
        host = urlsplit(base).hostname or ''
        direct = host.endswith('.maas.aliyuncs.com') or host == 'dashscope.aliyuncs.com'
        client = httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10),
                                   trust_env=transport is None and not direct, transport=transport)
        try:
            upstream = await client.send(client.build_request('POST', url, headers=headers, json=payload),
                                         stream=True)
        except httpx.RequestError:
            await client.aclose()
            return JSONResponse(status_code=502, content={'error': {'message': '知识库模型服务连接失败',
                                                                   'type': 'upstream_connection_error'}})
        content_type = upstream.headers.get('content-type', 'application/json')
        if 'text/event-stream' in content_type and upstream.is_success:
            async def chunks():
                try:
                    async for chunk in upstream.aiter_bytes():
                        yield chunk
                finally:
                    await upstream.aclose()
                    await client.aclose()
            return StreamingResponse(chunks(), media_type='text/event-stream',
                                     headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
        try:
            content = await upstream.aread()
            if (payload.get('stream') or google_action == 'streamGenerateContent') and upstream.is_success:
                return JSONResponse(status_code=502, content={'error': {
                    'message': '知识库 API 未提供流式响应，请使用支持工具调用和 SSE 的兼容接口',
                    'type': 'upstream_streaming_unsupported'}})
            return Response(content, status_code=upstream.status_code, media_type=content_type)
        finally:
            await upstream.aclose()
            await client.aclose()

    @app.post('/v1/chat/completions')
    async def chat(request: Request):
        return await forward(request)

    @app.post('/v1/models/{model}:generateContent')
    async def google_generate(request: Request, model: str):
        return await forward(request, google_action='generateContent')

    @app.post('/v1/models/{model}:streamGenerateContent')
    async def google_stream(request: Request, model: str):
        return await forward(request, google_action='streamGenerateContent')

    return app
