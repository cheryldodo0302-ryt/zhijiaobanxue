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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_contract import source_fingerprint


PROJECT_DIR = Path(__file__).resolve().parents[1]
WEB_DIR = PROJECT_DIR / "web"
WORKER_LOCK_PORT = 17654


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen[str]


def port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def port_is_bound(port: int) -> bool:
    """Check whether a port is occupied without queuing a connection."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return True
        return False


def worker_supports_current_contract() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", WORKER_LOCK_PORT), timeout=1) as connection:
            connection.settimeout(1)
            loaded_fingerprint = connection.recv(128).decode("ascii").strip()
        return loaded_fingerprint == source_fingerprint()
    except (OSError, UnicodeDecodeError):
        return False


def api_supports_current_contract() -> bool:
    """Reject a stale API instead of pairing it with the current Vue app."""
    try:
        # Local readiness checks must never be sent through a machine proxy.
        # A proxy may return 502 for 127.0.0.1 even when the local API is healthy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:8000/health", timeout=3) as response:
            health = json.load(response)
        if health.get("runtime_source_fingerprint") != source_fingerprint():
            return False
        with opener.open("http://127.0.0.1:8000/openapi.json", timeout=3) as response:
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
        message = f"[{name}] {line.rstrip()}"
        output_encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe_message = message.encode(output_encoding, errors="replace").decode(output_encoding)
        print(safe_message, flush=True)


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


def start_worker_or_reuse(
    managed: list[ManagedProcess],
    reused: list[str],
    env: dict[str, str],
) -> None:
    """Start one Worker, tolerating a concurrent launcher owning the lock first."""
    if port_is_bound(WORKER_LOCK_PORT):
        if not worker_supports_current_contract():
            raise RuntimeError(
                "端口 17654 正在运行旧版或非本项目 Worker；请先关闭旧启动窗口后再启动。"
            )
        reused.append(f"WORKER({WORKER_LOCK_PORT})")
        print(f"[WORKER] 端口 {WORKER_LOCK_PORT} 已有 Worker，直接复用。", flush=True)
        return

    worker = start_process(
        "WORKER",
        [sys.executable, "scripts/run_ingestion_worker.py"],
        PROJECT_DIR,
        env,
    )
    managed.append(worker)

    # run_ingestion_worker binds its single-instance socket before opening the
    # database. Give it a moment to acquire the lock so a simultaneous
    # launcher does not make the whole API/Web startup look failed.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        return_code = worker.process.poll()
        if return_code is not None:
            if port_is_bound(WORKER_LOCK_PORT):
                managed.remove(worker)
                reused.append(f"WORKER({WORKER_LOCK_PORT})")
                print(
                    f"[WORKER] 已有 Worker 持有 {WORKER_LOCK_PORT}，本次直接复用。",
                    flush=True,
                )
                return
            raise RuntimeError(f"Worker 启动失败，退出码 {return_code}。")
        if port_is_bound(WORKER_LOCK_PORT):
            return
        time.sleep(0.1)

    if worker.process.poll() is None and not port_is_bound(WORKER_LOCK_PORT):
        raise RuntimeError("Worker 启动超时，未能占用单实例锁端口 17654。")


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

        start_worker_or_reuse(managed, reused, env)

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
