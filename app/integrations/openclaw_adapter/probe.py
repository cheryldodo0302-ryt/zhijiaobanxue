"""Exercise the actual stdio transport without requiring a third-party client."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


def probe(*, user_id: str | None = None, database: str | None = None,
          timeout: float = 45) -> dict:
    entry = Path(__file__).resolve().parents[2] / "scripts" / "run_openclaw_mcp.py"
    command = [sys.executable, str(entry)]
    if user_id is not None:
        command += ["--user-id", user_id]
    if database is not None:
        command += ["--database", database]
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "zhijiao-probe", "version": "1.0.0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "zhijiao_connection_status", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "zhijiao_list_courses", "arguments": {}}},
    ]
    # Intentionally use another cwd: Explorer/other MCP hosts may start here.
    result = subprocess.run(command, input="".join(json.dumps(r) + "\n" for r in requests),
                            text=True, encoding="utf-8", capture_output=True,
                            cwd=entry.parents[2], timeout=timeout)
    if result.returncode:
        detail = result.stderr.strip().splitlines()[-1][:300] if result.stderr.strip() else ""
        raise RuntimeError("MCP 子进程启动失败" + (f"；{detail}" if detail else ""))
    replies = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    by_id = {reply.get("id"): reply for reply in replies}
    if set(by_id) != {1, 2, 3, 4}:
        raise RuntimeError("MCP 响应不完整，或标准输出被非协议文本污染")
    for reply in replies:
        if "error" in reply or reply.get("result", {}).get("isError"):
            raise RuntimeError("MCP 握手或工具调用失败，请使用 --check 检查绑定账号")
    tools = by_id[2]["result"]["tools"]
    connection = by_id[3]["result"]["structuredContent"]
    courses = by_id[4]["result"]["structuredContent"]["data"]
    if not connection.get("connected") or len(tools) != 7:
        raise RuntimeError("MCP 连接或工具目录不完整")
    return {"connected": True, "transport": "stdio", "tool_count": len(tools),
            "course_count": len(courses), "actor": connection["actor"],
            "database": connection["database"]}
