from pathlib import Path

import config


def _isolate_settings(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "BUNDLED_RELAY_ENV", tmp_path / "relay_client.env")
    monkeypatch.setattr(config, "SERVER_ENV", tmp_path / "server.env")
    monkeypatch.setattr(config, "USER_AI_ENV", tmp_path / "user_ai.env")
    for name in config._AI_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_bundled_relay_is_default_without_qwen_key(monkeypatch, tmp_path):
    _isolate_settings(monkeypatch, tmp_path)
    config.BUNDLED_RELAY_ENV.write_text(
        "ZHIJIAO_RELAY_URL=https://relay.example.com/compatible-mode/v1\n"
        "ZHIJIAO_RELAY_TOKEN=public-client-token\n",
        encoding="utf-8",
    )
    settings = config.get_ai_settings()
    assert settings["mode"] == "relay"
    assert settings["configured"] is True
    assert "DASHSCOPE" not in config.BUNDLED_RELAY_ENV.read_text(encoding="utf-8")


def test_local_custom_settings_override_bundled_relay(monkeypatch, tmp_path):
    _isolate_settings(monkeypatch, tmp_path)
    config.BUNDLED_RELAY_ENV.write_text(
        "ZHIJIAO_RELAY_URL=https://relay.example.com/compatible-mode/v1\n"
        "ZHIJIAO_RELAY_TOKEN=client-token\n",
        encoding="utf-8",
    )
    config.save_user_ai_settings(
        "custom",
        base_url="https://custom.example.com/v1/",
        api_key="local-secret",
        model="custom-model",
    )
    settings = config.get_ai_settings()
    assert settings["mode"] == "custom"
    assert settings["base_url"] == "https://custom.example.com/v1"
    assert settings["api_key"] == "local-secret"
    assert settings["model"] == "custom-model"


def test_gemini_custom_settings_are_detected_and_quotes_are_removed(monkeypatch, tmp_path):
    _isolate_settings(monkeypatch, tmp_path)
    config.save_user_ai_settings(
        "custom",
        base_url='https://generativelanguage.googleapis.com/v1beta/"',
        api_key="gemini-secret",
        model="gemini-2.5-flash",
        provider="auto",
    )
    settings = config.get_ai_settings()
    assert settings["provider"] == "gemini"
    assert settings["base_url"] == "https://generativelanguage.googleapis.com/v1beta"


def test_existing_quoted_gemini_endpoint_is_cleaned(monkeypatch, tmp_path):
    _isolate_settings(monkeypatch, tmp_path)
    config.USER_AI_ENV.write_text(
        "ZHIJIAO_AI_MODE=custom\n"
        "ZHIJIAO_CUSTOM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent\"\n"
        "ZHIJIAO_CUSTOM_API_KEY=secret\n"
        "ZHIJIAO_CUSTOM_MODEL=gemini-flash-latest\n",
        encoding="utf-8",
    )
    settings = config.get_ai_settings()
    assert settings["provider"] == "gemini"
    assert settings["base_url"].endswith(":generateContent")


def test_legacy_server_key_overrides_bundled_relay(monkeypatch, tmp_path):
    _isolate_settings(monkeypatch, tmp_path)
    config.BUNDLED_RELAY_ENV.write_text(
        "ZHIJIAO_AI_MODE=relay\n"
        "ZHIJIAO_RELAY_URL=https://relay.example.com/compatible-mode/v1\n"
        "ZHIJIAO_RELAY_TOKEN=client-token\n",
        encoding="utf-8",
    )
    config.SERVER_ENV.write_text(
        "DASHSCOPE_API_KEY=server-secret\n"
        "ZHIJIAO_AI_BASE_URL=https://qwen.example.com/v1\n"
        "ZHIJIAO_AI_MODEL=qwen-plus\n",
        encoding="utf-8",
    )
    settings = config.get_ai_settings()
    assert settings["mode"] == "qwen"
    assert settings["api_key"] == "server-secret"


def test_public_ai_settings_never_exposes_secret(monkeypatch, tmp_path):
    _isolate_settings(monkeypatch, tmp_path)
    config.save_user_ai_settings(
        "custom",
        base_url="https://custom.example.com/v1",
        api_key="never-return-this-secret",
        model="custom-model",
    )
    public = config.get_public_ai_settings()
    assert public["has_api_key"] is True
    assert "api_key" not in public
    assert "never-return-this-secret" not in repr(public)
