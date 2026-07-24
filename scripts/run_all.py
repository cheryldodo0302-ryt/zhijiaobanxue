"""Run the local API, ingestion worker, and Vue development server together."""

from __future__ import annotations

import os
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
WEB_DIR = PROJECT_DIR / "web"


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen[str]


def port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def api_supports_current_contract() -> bool:
    """Reject a stale API instead of pairing it with the current Vue app."""
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:8000/openapi.json", timeout=3
        ) as response:
            spec = json.load(response)
        paths = spec.get("paths") or {}
        folders = paths.get(
            "/api/v1/teacher/courses/{course_id}/question-folders"
        )
        semantic = paths.get(
            "/api/v1/teacher/documents/{document_id}/semantic-analysis"
        ) or {}
        return bool(folders and (semantic.get("post") or {}).get("requestBody"))
    except Exception:
        return False


def stream_output(name: str, process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        print(f"[{name}] {line.rstrip()}", flush=True)


def start_process(
    name: str,
    command: list[str],
    cwd: Path,
    env: dict[str, str],
) -> ManagedProcess:
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creationflags,
    )
    threading.Thread(
        target=stream_output,
        args=(name, process),
        daemon=True,
    ).start()
    print(f"[{name}] 已启动，PID={process.pid}", flush=True)
    return ManagedProcess(name=name, process=process)


def stop_process(item: ManagedProcess) -> None:
    if item.process.poll() is not None:
        return
    print(f"[{item.name}] 正在停止……", flush=True)
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(item.process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        item.process.terminate()
        try:
            item.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            item.process.kill()


def main() -> int:
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        print("未找到 Node.js/npm，请先安装并加入 PATH。", file=sys.stderr)
        return 2

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    managed: list[ManagedProcess] = []
    reused: list[str] = []
    try:
        if port_is_open(8000) and not api_supports_current_contract():
            print(
                "[API] 端口 8000 正在运行旧版或非本项目 API，不能与当前教师端混用。",
                file=sys.stderr,
                flush=True,
            )
            print(
                "[API] 请先关闭旧的启动窗口/进程，再重新执行 .\\start.ps1 -Mode all。",
                file=sys.stderr,
                flush=True,
            )
            return 3
        if port_is_open(8000):
            reused.append("API(8000)")
            print("[API] 端口 8000 已有服务，直接复用。", flush=True)
        else:
            managed.append(
                start_process(
                    "API",
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "api:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "8000",
                    ],
                    PROJECT_DIR,
                    env,
                )
            )

        managed.append(
            start_process(
                "WORKER",
                [sys.executable, "scripts/run_ingestion_worker.py"],
                PROJECT_DIR,
                env,
            )
        )

        if port_is_open(5173):
            reused.append("Vue(5173)")
            print("[WEB] 端口 5173 已有服务，直接复用。", flush=True)
        else:
            managed.append(
                start_process(
                    "WEB",
                    [
                        npm,
                        "run",
                        "dev",
                        "--",
                        "--host",
                        "127.0.0.1",
                    ],
                    WEB_DIR,
                    env,
                )
            )

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            failed = next(
                (item for item in managed if item.process.poll() is not None),
                None,
            )
            if failed:
                print(
                    f"[{failed.name}] 启动失败，退出码 {failed.process.returncode}。",
                    file=sys.stderr,
                )
                return failed.process.returncode or 1
            if port_is_open(8000) and port_is_open(5173):
                break
            time.sleep(0.5)
        else:
            print("服务启动超时，请查看上方对应日志。", file=sys.stderr)
            return 1

        print("\n系统已就绪：", flush=True)
        print("  教师端：http://127.0.0.1:5173", flush=True)
        print("  API 文档：http://127.0.0.1:8000/docs", flush=True)
        if reused:
            print(f"  已复用外部进程：{', '.join(reused)}", flush=True)
        print("按 Ctrl+C 一次即可停止本启动器拉起的全部服务。\n", flush=True)

        while True:
            for item in managed:
                return_code = item.process.poll()
                if return_code is not None:
                    print(
                        f"[{item.name}] 意外退出，退出码 {return_code}。",
                        file=sys.stderr,
                    )
                    return return_code or 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n收到停止指令。", flush=True)
        return 0
    finally:
        for item in reversed(managed):
            stop_process(item)


if __name__ == "__main__":
    raise SystemExit(main())
