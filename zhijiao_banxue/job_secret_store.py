from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from config import DATA_DIR


KEY_PATH = DATA_DIR / ".job-secret.key"


def _fernet() -> Fernet:
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not KEY_PATH.exists():
        KEY_PATH.write_bytes(Fernet.generate_key())
        try:
            KEY_PATH.chmod(0o600)
        except OSError:
            pass
    return Fernet(KEY_PATH.read_bytes().strip())


def encrypt_job_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii") if value else ""


def decrypt_job_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, OSError) as exc:
        raise RuntimeError("任务 API Key 无法解密，请重新提交分析任务") from exc
