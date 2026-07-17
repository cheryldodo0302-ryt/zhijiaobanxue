from unittest.mock import Mock

import pytest

from llm_provider import GeminiProvider, OpenAICompatibleProvider


def _response(data, status_code=200):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = data
    response.text = ""
    return response


def test_openai_full_endpoint_is_not_duplicated():
    provider = OpenAICompatibleProvider("key", "https://example.com/v1/chat/completions", "model")
    provider.session.post = Mock(return_value=_response({"choices": [{"message": {"content": "ok"}}]}))
    assert provider.generate("system", "user") == "ok"
    assert provider.session.post.call_args.args[0] == "https://example.com/v1/chat/completions"


def test_gemini_root_url_uses_native_payload_and_header():
    provider = GeminiProvider("gemini-key", "https://generativelanguage.googleapis.com/v1beta", "gemini-2.5-flash")
    provider.session.post = Mock(return_value=_response({
        "candidates": [{"content": {"parts": [{"text": "连接成功"}]}}]
    }))
    assert provider.generate("system", "user") == "连接成功"
    call = provider.session.post.call_args
    assert call.args[0].endswith("/models/gemini-2.5-flash:generateContent")
    assert call.kwargs["headers"]["x-goog-api-key"] == "gemini-key"
    assert call.kwargs["json"]["contents"][0]["parts"][0]["text"] == "user"


def test_gemini_full_endpoint_is_not_modified():
    endpoint = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    provider = GeminiProvider("key", endpoint, "ignored")
    assert provider._endpoint() == endpoint


def test_gemini_image_extraction_uses_inline_data():
    provider = GeminiProvider("key", "https://generativelanguage.googleapis.com/v1beta", "gemini-2.5-flash")
    provider.session.post = Mock(return_value=_response({
        "candidates": [{"content": {"parts": [{"text": "图片文字"}]}}]
    }))
    assert provider.extract_image_text(b"image", "image/png") == "图片文字"
    payload = provider.session.post.call_args.kwargs["json"]
    inline_data = payload["contents"][0]["parts"][0]["inlineData"]
    assert inline_data["mimeType"] == "image/png"
    assert inline_data["data"] == "aW1hZ2U="


def test_gemini_invalid_key_error_explains_provider_mismatch():
    provider = GeminiProvider("wrong-key", "https://generativelanguage.googleapis.com/v1beta", "gemini-model")
    provider.session.post = Mock(return_value=_response({
        "error": {"message": "API key not valid. Please pass a valid API key."}
    }, status_code=400))
    with pytest.raises(RuntimeError, match="千问、OpenAI 或其他平台"):
        provider.generate("system", "user")
