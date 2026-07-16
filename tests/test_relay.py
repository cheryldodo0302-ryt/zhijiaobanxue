from fastapi.testclient import TestClient

from relay import app as relay_module


class FakeUpstreamResponse:
    status_code = 200

    @staticmethod
    def json():
        return {"choices": [{"message": {"content": "relay ok"}}]}


def test_relay_rejects_invalid_token(monkeypatch):
    monkeypatch.setattr(relay_module, "QWEN_API_KEY", "server-qwen-key")
    monkeypatch.setattr(relay_module, "RELAY_CLIENT_TOKEN", "client-token")
    client = TestClient(relay_module.app)
    response = client.post(
        "/compatible-mode/v1/chat/completions",
        headers={"Authorization": "Bearer wrong"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 401


def test_relay_keeps_qwen_key_server_side_and_forces_model(monkeypatch):
    monkeypatch.setattr(relay_module, "QWEN_API_KEY", "server-qwen-key")
    monkeypatch.setattr(relay_module, "RELAY_CLIENT_TOKEN", "client-token")
    captured = {}

    def fake_post(url, headers, json, timeout, verify):
        captured.update({"url": url, "headers": headers, "json": json})
        return FakeUpstreamResponse()

    monkeypatch.setattr(relay_module.session, "post", fake_post)
    client = TestClient(relay_module.app)
    response = client.post(
        "/compatible-mode/v1/chat/completions",
        headers={"Authorization": "Bearer client-token"},
        json={
            "model": "client-controlled-model",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 999999,
        },
    )
    assert response.status_code == 200
    assert captured["headers"]["Authorization"] == "Bearer server-qwen-key"
    assert captured["json"]["model"] == relay_module.QWEN_TEXT_MODEL
    assert captured["json"]["max_tokens"] == 6000


def test_relay_routes_images_to_server_ocr_model(monkeypatch):
    monkeypatch.setattr(relay_module, "QWEN_API_KEY", "server-qwen-key")
    monkeypatch.setattr(relay_module, "RELAY_CLIENT_TOKEN", "client-token")
    captured = {}

    def fake_post(url, headers, json, timeout, verify):
        captured["model"] = json["model"]
        return FakeUpstreamResponse()

    monkeypatch.setattr(relay_module.session, "post", fake_post)
    client = TestClient(relay_module.app)
    response = client.post(
        "/compatible-mode/v1/chat/completions",
        headers={"Authorization": "Bearer client-token"},
        json={"messages": [{
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}],
        }]},
    )
    assert response.status_code == 200
    assert captured["model"] == relay_module.QWEN_OCR_MODEL
