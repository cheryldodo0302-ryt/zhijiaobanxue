from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from campus_service import PermissionDenied, ValidationError
from database import LearningDatabase


class AuthService:
    def __init__(self, db: LearningDatabase, secret_path: Path | None = None):
        self.db = db
        self.passwords = PasswordHasher()
        self.secret = os.environ.get("ZHIJIAO_JWT_SECRET", "").strip() or self._local_secret(secret_path)
        self.issuer = "zhijiao-banxue"
        self.document_token_minutes = max(1, int(os.environ.get("ZHIJIAO_PREVIEW_TOKEN_MINUTES", "5")))
        self._login_attempts: dict[str, deque[float]] = defaultdict(deque)
        self._login_lock = threading.Lock()
        self.login_limit = max(3, int(os.environ.get("ZHIJIAO_LOGIN_LIMIT", "8")))

    def _local_secret(self, secret_path: Path | None) -> str:
        path = secret_path or self.db.db_path.parent / "auth_secret"
        if path.exists():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        value = secrets.token_urlsafe(48)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(value, encoding="utf-8")
        temporary.replace(path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return value

    def create_user(self, username: str, password: str, role: str, display_name: str = "",
                    *, student_number: str | None = None,
                    must_change_password: bool = False) -> dict[str, Any]:
        username = username.strip().lower()
        if not username or len(username) > 64:
            raise ValidationError("用户名不能为空且不能超过 64 个字符")
        if role not in {"teacher", "student"}:
            raise ValidationError("用户角色不合法")
        if len(password) < 10:
            raise ValidationError("密码至少需要 10 个字符")
        user_id = f"{'t' if role == 'teacher' else 's'}_{uuid.uuid4().hex[:16]}"
        try:
            self.db.execute(
                """INSERT INTO users(user_id,username,password_hash,role,display_name,student_number,must_change_password)
                   VALUES(?,?,?,?,?,?,?)""",
                (user_id, username, self.passwords.hash(password), role, display_name.strip(),
                 student_number.strip() if student_number else None, 1 if must_change_password else 0),
            )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise ValidationError("用户名已存在") from exc
            raise
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> dict[str, Any]:
        user = self.db.fetch_one(
            """SELECT user_id,username,role,display_name,status,student_number,
                      must_change_password,password_changed_at,created_at
               FROM users WHERE user_id=?""", (user_id,)
        )
        if not user:
            raise PermissionDenied("用户不存在")
        return user

    def login(self, username: str, password: str, client_id: str = "local") -> tuple[dict[str, Any], str, str]:
        normalized = username.strip().lower()
        key = f"{client_id[:80]}:{normalized[:64]}"
        now = time.monotonic()
        with self._login_lock:
            attempts = self._login_attempts[key]
            while attempts and attempts[0] <= now - 300:
                attempts.popleft()
            if len(attempts) >= self.login_limit:
                raise PermissionDenied("登录尝试过于频繁，请稍后再试")
        row = self.db.fetch_one("SELECT * FROM users WHERE username=?", (normalized,))
        if not row or row["status"] != "active":
            with self._login_lock:
                self._login_attempts[key].append(now)
            raise PermissionDenied("用户名或密码错误")
        try:
            self.passwords.verify(row["password_hash"], password)
        except VerifyMismatchError as exc:
            with self._login_lock:
                self._login_attempts[key].append(now)
            raise PermissionDenied("用户名或密码错误") from exc
        with self._login_lock:
            self._login_attempts.pop(key, None)
        user = self.get_user(row["user_id"])
        return user, self._access_token(user), self._refresh_token(user)

    def _encode(self, user: dict[str, Any], token_type: str, lifetime: timedelta, token_id: str) -> str:
        now = datetime.now(timezone.utc)
        return jwt.encode(
            {"sub": user["user_id"], "role": user["role"], "type": token_type, "jti": token_id,
             "iss": self.issuer, "iat": now, "exp": now + lifetime},
            self.secret,
            algorithm="HS256",
        )

    def _access_token(self, user: dict[str, Any]) -> str:
        return self._encode(user, "access", timedelta(minutes=15), uuid.uuid4().hex)

    def _refresh_token(self, user: dict[str, Any]) -> str:
        token_id = uuid.uuid4().hex
        expires = datetime.now(timezone.utc) + timedelta(days=7)
        token = self._encode(user, "refresh", timedelta(days=7), token_id)
        self.db.execute(
            "INSERT INTO refresh_tokens(token_id,user_id,token_hash,expires_at) VALUES(?,?,?,?)",
            (token_id, user["user_id"], hashlib.sha256(token.encode()).hexdigest(), expires.isoformat()),
        )
        return token

    def decode(self, token: str, expected_type: str = "access") -> dict[str, Any]:
        try:
            payload = jwt.decode(token, self.secret, algorithms=["HS256"], issuer=self.issuer)
        except jwt.PyJWTError as exc:
            raise PermissionDenied("登录状态无效或已过期") from exc
        if payload.get("type") != expected_type:
            raise PermissionDenied("令牌类型不合法")
        return payload

    def authenticate(self, token: str) -> dict[str, Any]:
        user = self.get_user(str(self.decode(token)["sub"]))
        if user.get("status") != "active":
            raise PermissionDenied("账号已停用")
        return user

    def issue_document_token(self, user: dict[str, Any], document_id: str) -> str:
        now = datetime.now(timezone.utc)
        return jwt.encode(
            {
                "sub": user["user_id"], "role": user["role"], "type": "document_source",
                "document_id": document_id, "jti": uuid.uuid4().hex, "iss": self.issuer,
                "iat": now, "exp": now + timedelta(minutes=self.document_token_minutes),
            },
            self.secret,
            algorithm="HS256",
        )

    def authenticate_document_token(self, token: str, document_id: str) -> dict[str, Any]:
        payload = self.decode(token, "document_source")
        if payload.get("document_id") != document_id:
            raise PermissionDenied("资料预览令牌与文件不匹配")
        user = self.get_user(str(payload["sub"]))
        if user.get("status") != "active":
            raise PermissionDenied("账号已停用")
        return user

    def refresh(self, token: str) -> tuple[dict[str, Any], str, str]:
        payload = self.decode(token, "refresh")
        digest = hashlib.sha256(token.encode()).hexdigest()
        stored = self.db.fetch_one(
            "SELECT * FROM refresh_tokens WHERE token_id=? AND token_hash=? AND revoked_at IS NULL",
            (payload["jti"], digest),
        )
        if not stored:
            raise PermissionDenied("刷新令牌已失效")
        self.db.execute("UPDATE refresh_tokens SET revoked_at=CURRENT_TIMESTAMP WHERE token_id=?", (payload["jti"],))
        user = self.get_user(str(payload["sub"]))
        if user.get("status") != "active":
            raise PermissionDenied("账号已停用")
        return user, self._access_token(user), self._refresh_token(user)

    def change_password(self, user: dict[str, Any], old_password: str,
                        new_password: str) -> tuple[dict[str, Any], str, str]:
        if len(new_password) < 10:
            raise ValidationError("新密码至少需要 10 个字符")
        if old_password == new_password:
            raise ValidationError("新密码不能与初始密码相同")
        row = self.db.fetch_one("SELECT password_hash FROM users WHERE user_id=?", (user["user_id"],))
        if not row:
            raise PermissionDenied("用户不存在")
        try:
            self.passwords.verify(row["password_hash"], old_password)
        except VerifyMismatchError as exc:
            raise PermissionDenied("原密码错误") from exc
        with self.db.connect() as conn:
            conn.execute(
                """UPDATE users SET password_hash=?,must_change_password=0,
                   password_changed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE user_id=?""",
                (self.passwords.hash(new_password), user["user_id"]),
            )
            conn.execute(
                "UPDATE refresh_tokens SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=? AND revoked_at IS NULL",
                (user["user_id"],),
            )
        updated = self.get_user(str(user["user_id"]))
        return updated, self._access_token(updated), self._refresh_token(updated)

    def revoke(self, token: str) -> None:
        try:
            payload = self.decode(token, "refresh")
        except PermissionDenied:
            return
        self.db.execute("UPDATE refresh_tokens SET revoked_at=CURRENT_TIMESTAMP WHERE token_id=?", (payload["jti"],))
