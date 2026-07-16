import socket
from urllib.parse import urlparse

from config import get_ai_settings
from llm_provider import backend_provider_status, build_backend_provider


def main() -> None:
    settings = get_ai_settings()
    status = backend_provider_status()
    base_url = str(settings["base_url"])
    print(f"Mode: {status['mode']}")
    print(f"Provider: {status['provider']}")
    print(f"Model: {status['model']}")
    print(f"Endpoint: {base_url}/chat/completions")
    if not status["configured"]:
        raise SystemExit("FAILED: AI service configuration is incomplete.")
    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, port)})
        print(f"DNS: {host} -> {', '.join(addresses)}")
        with socket.create_connection((host, port), timeout=5):
            print(f"TCP {port}: {host} reachable")
    except OSError as exc:
        raise SystemExit(f"FAILED: AI service host is unreachable: {exc}") from exc
    try:
        provider = build_backend_provider()
        response = provider.generate(
            "You are a connectivity checker. Follow the user instruction exactly.",
            "只回复：智能服务连接成功",
        )
    except Exception as exc:
        raise SystemExit(f"FAILED: {exc}") from exc
    print(f"Connected endpoint: {getattr(provider, 'last_endpoint', base_url)}")
    print(f"SUCCESS: {response[:200]}")


if __name__ == "__main__":
    main()
