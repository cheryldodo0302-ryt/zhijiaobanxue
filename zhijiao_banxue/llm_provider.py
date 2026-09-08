import json
from abc import ABC, abstractmethod
from typing import Any
from urllib import request


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Return plain text generated from the supplied prompts."""


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: str, model: str, timeout: int = 60):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        payload = json.dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": user_prompt}],
            "temperature": 0.2,
        }).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/chat/completions", data=payload, method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            raise RuntimeError(f"LLM 接口调用失败：{exc}") from exc


class MockProvider(LLMProvider):
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        marker = "【课程证据】"
        evidence = user_prompt.split(marker, 1)[-1] if marker in user_prompt else user_prompt
        lines = [line.strip() for line in evidence.splitlines()
                 if line.strip() and not line.lstrip().startswith(("来源", "问题"))]
        summary = " ".join(lines[:3])[:500]
        return f"根据课程资料，{summary}" if summary else "课程资料中没有足够证据回答该问题。"


def build_provider(kind: str, api_key: str = "", base_url: str = "", model: str = "") -> LLMProvider:
    if kind == "OpenAI 兼容接口":
        if not all((api_key, base_url, model)):
            raise ValueError("请完整填写 API Key、Base URL 和模型名称。")
        return OpenAICompatibleProvider(api_key, base_url, model)
    return MockProvider()

