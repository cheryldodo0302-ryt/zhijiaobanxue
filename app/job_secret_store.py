from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

from cryptography.fernet import Fernet, InvalidToken

from config import DATA_DIR


KEY_PATH = DATA_DIR / ".job-secret.key"


def _fernet(*, create: bool = True, key_path: Path | None = None) -> Fernet:
    path = key_path or KEY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        if not create:
            raise FileNotFoundError("缺少 API Key 解密文件")
        path.write_bytes(Fernet.generate_key())
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return Fernet(path.read_bytes().strip())


def encrypt_job_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii") if value else ""


def decrypt_job_secret(value: str, *, key_path: Path | None = None) -> str:
    if not value:
        return ""
    try:
        return _fernet(create=False, key_path=key_path).decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, OSError) as exc:
        raise RuntimeError("已保存的 API Key 无法解密。请从原电脑复制完整 data 目录（含 .job-secret.key），或重新保存 API Key") from exc


def validate_saved_ai_secrets(db_path: Path) -> None:
    """Check the copied database/key pair without exposing credentials."""
    if not db_path.is_file():
        return
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("account_ai_settings", "teacher_ai_settings"):
            if table in tables:
                for (encrypted,) in conn.execute(
                    f"SELECT api_key_encrypted FROM {table} WHERE api_key_encrypted<>''"
                ):
                    decrypt_job_secret(encrypted)


if __name__ == "__main__":
    from config import DB_PATH
    try:
        validate_saved_ai_secrets(DB_PATH)
    except (RuntimeError, sqlite3.Error) as exc:
        print(f"[配置] {exc}")
        raise SystemExit(1)
    print("[配置] 已保存的模型与 API Key 可读取。")
