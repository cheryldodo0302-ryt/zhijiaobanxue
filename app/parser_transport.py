"""Shared connection policy for the two remote parsing clients."""
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import get_runtime_setting


def create_parser_session(base_url: str) -> requests.Session:
    session = requests.Session()
    bypass = get_runtime_setting("ZHIJIAO_PARSER_BYPASS_PROXY", "0").lower() in {"1", "true", "yes", "on"}
    if bypass or urlparse(base_url).hostname in {"127.0.0.1", "localhost", "::1"}:
        session.trust_env = False
    # Retry transient health-check resets. Never replay an uploaded POST after
    # a read failure: the server may already be running the expensive parse.
    retries = Retry(total=2, connect=2, read=2, other=0, status=0,
                    allowed_methods=frozenset({"GET", "HEAD"}), backoff_factor=0.3)
    for scheme in ("http://", "https://"):
        session.mount(scheme, HTTPAdapter(max_retries=retries))
    return session

PARSER_FAILURES = {
    "authentication": "认证失败，请核对服务令牌和访问权限。",
    "tls": "TLS 验证失败，请核对服务域名及证书链。",
    "timeout": "请求超时，请检查服务负载和网络后重试。",
    "connection": "连接失败，请核对域名、端口及网络。",
    "service_error": "解析服务异常，请检查服务状态后重试。",
    "parse_error": "解析结果无效，请检查文件格式或服务返回的内容。",
}


def parser_failure(exc):
    if isinstance(exc, requests.exceptions.SSLError):
        return "tls"
    if isinstance(exc, (requests.Timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, (requests.ConnectionError, ConnectionError)):
        return "connection"
    response = getattr(exc, "response", None)
    if response is not None:
        if response.status_code in (401, 403):
            return "authentication"
        if response.status_code >= 500 or response.status_code == 429:
            return "service_error"
    return "parse_error"
