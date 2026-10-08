from __future__ import annotations

import argparse
import json
import sys
import traceback
from typing import Any, TextIO

from .adapter import OpenClawAdapter, OpenClawToolError


SERVER_INFO = {"name": "zhijiao-database", "version": "1.0.0"}


class StdioMcpServer:
    """Dependency-free MCP stdio transport used by OpenClaw."""

    def __init__(self, adapter: OpenClawAdapter, stdin: TextIO, stdout: TextIO):
        self.adapter = adapter
        self.stdin = stdin
        self.stdout = stdout

    def serve(self) -> None:
        for raw_line in self.stdin:
            if not raw_line.strip():
                continue
            request: Any = None
            try:
                request = json.loads(raw_line)
                response = self.handle(request)
            except Exception as exc:  # keep the process alive after one bad request
                response = self._error(request.get("id") if isinstance(request, dict) else None,
                                       -32603, "MCP 服务内部错误", str(exc))
                traceback.print_exc(file=sys.stderr)
            if response is not None:
                self.stdout.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
                self.stdout.flush()

    def handle(self, request: Any) -> dict[str, Any] | None:
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            return self._error(None, -32600, "无效的 JSON-RPC 请求")
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if request_id is None:  # MCP notifications intentionally have no response
            return None
        if method == "initialize":
            requested_version = params.get("protocolVersion") if isinstance(params, dict) else None
            return self._result(request_id, {
                "protocolVersion": requested_version or "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": "先列出课程，再使用 course_id 调用智教伴学数据库工具。",
            })
        if method == "ping":
            return self._result(request_id, {})
        if method == "tools/list":
            return self._result(request_id, {"tools": self.adapter.tool_definitions()})
        if method == "tools/call":
            if not isinstance(params, dict):
                return self._error(request_id, -32602, "工具参数必须是对象")
            try:
                value = self.adapter.invoke(params)
                structured = value if isinstance(value, dict) else {"data": value}
                return self._result(request_id, {
                    "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, default=str)}],
                    "structuredContent": structured, "isError": False,
                })
            except OpenClawToolError as exc:
                return self._result(request_id, {
                    "content": [{"type": "text", "text": str(exc)}], "isError": True,
                })
        return self._error(request_id, -32601, f"不支持的方法：{method}")

    @staticmethod
    def _result(request_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}


def main() -> int:
    # MCP stdio is UTF-8 JSON regardless of the Windows console code page.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="智教伴学 OpenClaw MCP stdio 服务")
    parser.add_argument("--check", action="store_true", help="只检查数据库与绑定账号")
    args = parser.parse_args()
    try:
        adapter = OpenClawAdapter.from_environment()
    except Exception as exc:
        print(f"OpenClaw MCP 启动失败：{exc}", file=sys.stderr)
        return 2
    if args.check:
        print(json.dumps(adapter.invoke({"name": "zhijiao_connection_status"}), ensure_ascii=False))
        return 0
    StdioMcpServer(adapter, sys.stdin, sys.stdout).serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
