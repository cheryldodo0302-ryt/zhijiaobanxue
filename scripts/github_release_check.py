"""Fail a GitHub release when runtime data or live credentials are tracked.

The scanner deliberately reports only paths and revisions, never matched secret
values. It checks both the current index and every locally available commit.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
REPOSITORY = PROJECT
PROJECT_PREFIX = ""
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pem", ".key"}
FORBIDDEN_NAMES = {"server.env", "user_ai.env", "relay_client.env", "demo_credentials.txt"}
SECRET_PATTERNS = (
    re.compile(rb"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(
        rb"(?im)^[ \t]*(?:DASHSCOPE_API_KEY|ZHIJIAO_RELAY_TOKEN|ZHIJIAO_CUSTOM_API_KEY|"
        rb"ZHIJIAO_JWT_SECRET)[ \t]*=[ \t]*(?![ \t]*(?:$|replace|example|your|<|\xe4\xbd\xa0\xe7\x9a\x84|\xe4\xb8\x8a\xe4\xb8\x80\xe6\xad\xa5))[^\s#]{12,}"
    ),
)


def git(*args: str, check: bool = True) -> bytes:
    return subprocess.run(
        ["git", "-C", str(REPOSITORY), *args],
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ).stdout


def forbidden_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    relative = normalized.removeprefix(PROJECT_PREFIX)
    parts = Path(relative).parts
    return (
        relative.startswith(("data/", "uploads/", "exports/", "test_runtime/"))
        or Path(relative).suffix.lower() in FORBIDDEN_SUFFIXES
        or Path(relative).name in FORBIDDEN_NAMES
        or "node_modules" in parts
    )


def contains_secret(content: bytes) -> bool:
    return any(pattern.search(content) for pattern in SECRET_PATTERNS)


def current_index_findings() -> set[str]:
    findings: set[str] = set()
    paths = git("ls-files", "-z").decode("utf-8", errors="replace").split("\0")
    for path in filter(None, paths):
        if forbidden_path(path):
            findings.add(f"当前索引包含禁止发布的文件：{path}")
            continue
        disk_path = REPOSITORY / path
        try:
            if disk_path.is_file() and disk_path.stat().st_size <= 2_000_000 and contains_secret(disk_path.read_bytes()):
                findings.add(f"当前索引疑似包含真实密钥：{path}")
        except OSError:
            continue
    return findings


def history_findings() -> set[str]:
    findings: set[str] = set()
    objects = git("rev-list", "--objects", "--all", check=False).decode(
        "utf-8", errors="replace"
    ).splitlines()
    seen_blobs: set[str] = set()
    for item in objects:
        object_id, separator, path = item.partition(" ")
        if not separator or not path:
            continue
        if forbidden_path(path):
            findings.add(f"历史对象 {object_id[:12]} 包含禁止发布的文件：{path}")
            continue
        if object_id in seen_blobs:
            continue
        seen_blobs.add(object_id)
        size_text = git("cat-file", "-s", object_id, check=False).decode().strip()
        if not size_text.isdigit() or int(size_text) > 2_000_000:
            continue
        content = git("cat-file", "blob", object_id, check=False)
        if contains_secret(content):
            findings.add(f"历史对象 {object_id[:12]} 疑似包含真实密钥：{path}")
    return findings


def main() -> int:
    findings = current_index_findings() | history_findings()
    if findings:
        print("GitHub 发布检查失败（为安全起见不显示密钥内容）：", file=sys.stderr)
        for finding in sorted(findings):
            print(f"- {finding}", file=sys.stderr)
        print("请先撤销泄露令牌并按 GITHUB_RELEASE_SECURITY.md 清理历史。", file=sys.stderr)
        return 1
    print("GitHub 发布检查通过：未追踪运行数据库、上传资料或疑似真实密钥。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
