"""Double-click entry point: packaged UI, API, worker and verified browser launch."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_contract import source_fingerprint, runtime_instance_id
from scripts.run_all import (PROJECT_DIR, start_process, stop_process, port_is_bound,
                             start_worker_or_reuse)


def matches(port):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            health = json.load(response)
        return (health.get("runtime_source_fingerprint") == source_fingerprint()
                and health.get("runtime_instance_id") == runtime_instance_id()
                and health.get("desktop_ui") is True)
    except Exception:
        return False


def main():
    managed, reused = [], []
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "ZHIJIAO_DESKTOP": "1"}
    try:
        # Reuse only the same installation and source version; choose another
        # free port when an unrelated app/old copy owns the normal entry point.
        ports = range(5173, 5194)
        port = next((p for p in ports if port_is_bound(p) and matches(p)), None)
        if port is None:
            port = next((p for p in ports if not port_is_bound(p)), None)
            if port is None:
                raise RuntimeError("本地入口端口均被占用，请关闭旧的智教伴学窗口后重试。")
            api = start_process("API", [sys.executable, "-m", "uvicorn", "desktop_app:app",
                                       "--host", "127.0.0.1", "--port", str(port)], PROJECT_DIR, env)
            managed.append(api)
            deadline = time.monotonic() + 90
            while not matches(port):
                if api.process.poll() is not None:
                    raise RuntimeError("本地服务启动失败，请查看上方日志。")
                if time.monotonic() >= deadline:
                    raise RuntimeError("本地服务启动超时。")
                time.sleep(0.5)
        start_worker_or_reuse(managed, reused, env)
        url = f"http://127.0.0.1:{port}"
        print(f"\n智教伴学已就绪：{url}\n保留此窗口，按 Ctrl+C 停止服务。", flush=True)
        webbrowser.open(url)
        if not managed:
            return 0
        while True:
            for item in managed:
                if item.process.poll() is not None:
                    raise RuntimeError(f"{item.name} 意外退出，请重新双击智教伴学.exe。")
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    finally:
        for item in reversed(managed):
            stop_process(item)


if __name__ == "__main__":
    raise SystemExit(main())
