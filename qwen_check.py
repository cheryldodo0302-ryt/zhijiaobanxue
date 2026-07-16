import socket
from urllib.parse import urlparse

from config import AI_BASE_URL, AI_MODEL
from llm_provider import backend_provider_status, build_backend_provider


def main() -> None:
    status = backend_provider_status()
    print(f"Provider: {status['provider']}")
    print(f"Model: {AI_MODEL}")
    print(f"Endpoint: {AI_BASE_URL}/chat/completions")
    host = urlparse(AI_BASE_URL).hostname or ""
    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, 443)})
        print(f"DNS: {host} -> {', '.join(addresses)}")
        if any(address.startswith(("198.18.", "198.19.")) for address in addresses):
            print("WARNING: The domain resolves to the 198.18.0.0/15 virtual test network. "
                  "Run deployment outside the isolated network or configure the school's HTTPS proxy.")
    except OSError as exc:
        print(f"DNS lookup failed: {exc}")
    reachable = []
    for check_host in (host, "dashscope.aliyuncs.com"):
        try:
            with socket.create_connection((check_host, 443), timeout=5):
                reachable.append(check_host)
            print(f"TCP 443: {check_host} reachable")
        except OSError as exc:
            print(f"TCP 443: {check_host} blocked or timed out ({exc})")
    if not reachable:
        raise SystemExit(
            "FAILED: Both Qwen HTTPS hosts are blocked before TLS/authentication. "
            "Allow outbound TCP 443, switch networks, or configure HTTPS_PROXY."
        )
    if not status["configured"]:
        raise SystemExit("FAILED: DASHSCOPE_API_KEY was not loaded by the backend.")
    try:
        provider = build_backend_provider()
        response = provider.generate(
            "You are a connectivity checker. Follow the user instruction exactly.",
            "只回复：千问连接成功",
        )
    except Exception as exc:
        raise SystemExit(f"FAILED: {exc}") from exc
    print(f"Connected endpoint: {getattr(provider, 'last_endpoint', AI_BASE_URL)}")
    print(f"SUCCESS: {response[:200]}")


if __name__ == "__main__":
    main()
