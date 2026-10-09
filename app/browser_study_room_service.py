from __future__ import annotations

import json
import hashlib
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from config import DATA_DIR
from study_room_ai import StudyMetrics


class StudyRoomUnavailable(RuntimeError):
    """Raised when a student has no active browser study session."""


class BrowserStudyRoomService:
    """浏览器本地识别 + 后端脱敏状态聚合的 AI 自习室服务。"""

    def __init__(self, db_path: Path | None = None, campus=None) -> None:
        self.campus = campus
        self.db_path = Path(db_path or (DATA_DIR / "study_room.db"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            URL.create("sqlite+pysqlite", database=str(self.db_path.resolve())),
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
        )
        self._lock = threading.RLock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._completed: dict[str, dict[str, Any]] = {}
        with self.engine.begin() as connection:
            connection.execute(text("""CREATE TABLE IF NOT EXISTS study_room_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""))
            connection.execute(text("""CREATE TABLE IF NOT EXISTS study_sharing_grants (
                grant_id TEXT PRIMARY KEY, student_id TEXT NOT NULL, course_id TEXT NOT NULL,
                class_id TEXT NOT NULL, granted_at TEXT NOT NULL, revoked_at TEXT
            )"""))
            connection.execute(text("""CREATE UNIQUE INDEX IF NOT EXISTS idx_study_active_grant
                ON study_sharing_grants(student_id,course_id,class_id) WHERE revoked_at IS NULL"""))

    def _sharing_scope(self, actor, course_id, class_id, student_id=None):
        from class_task_service import ClassTaskService
        if self.campus is None:
            raise StudyRoomUnavailable("自习共享服务尚未配置")
        return ClassTaskService(self.campus).scope(actor, course_id, class_id, student_id)

    def grants(self, actor):
        from campus_service import PermissionDenied
        if actor.get("role") != "student":
            raise PermissionDenied("只能管理自己的自习授权")
        with self.engine.connect() as connection:
            return [dict(r) for r in connection.execute(text("SELECT * FROM study_sharing_grants WHERE student_id=:uid AND revoked_at IS NULL"), {"uid": actor["user_id"]}).mappings()]

    def grant(self, actor, course_id, class_id):
        from campus_service import PermissionDenied
        from class_task_service import stamp, utc_now
        if actor.get("role") != "student":
            raise PermissionDenied("只能管理自己的自习授权")
        self._sharing_scope(actor, course_id, class_id)
        with self._lock, self.engine.begin() as connection:
            connection.execute(text("BEGIN IMMEDIATE"))
            old = connection.execute(text("""SELECT * FROM study_sharing_grants WHERE student_id=:uid
                AND course_id=:course AND class_id=:class_id AND revoked_at IS NULL"""),
                {"uid": actor["user_id"], "course": course_id, "class_id": class_id}).mappings().first()
            if old:
                return dict(old)
            result = {"grant_id": uuid4().hex, "student_id": actor["user_id"], "course_id": course_id,
                      "class_id": class_id, "granted_at": stamp(utc_now()), "revoked_at": None}
            connection.execute(text("INSERT INTO study_sharing_grants VALUES(:grant_id,:student_id,:course_id,:class_id,:granted_at,:revoked_at)"), result)
            return result

    def revoke(self, actor, grant_id):
        from campus_service import PermissionDenied
        from class_task_service import stamp, utc_now
        if actor.get("role") != "student":
            raise PermissionDenied("只能管理自己的自习授权")
        with self._lock, self.engine.begin() as connection:
            row = connection.execute(text("SELECT * FROM study_sharing_grants WHERE grant_id=:id AND student_id=:uid"), {"id": grant_id, "uid": actor["user_id"]}).mappings().first()
            if not row:
                raise PermissionDenied("无权撤销该授权")
            connection.execute(text("UPDATE study_sharing_grants SET revoked_at=:now WHERE grant_id=:id"), {"now": stamp(utc_now()), "id": grant_id})
        self._invalidate_portraits(actor["user_id"])

    def _invalidate_portraits(self, student_id):
        if self.campus:
            self.campus.db.execute("UPDATE student_portrait_evaluations SET invalidated=1 WHERE student_id=?", (student_id,))

    def shared_summary(self, actor, course_id, class_id, student_id, start_at, end_at):
        from class_task_service import timestamp
        from campus_service import PermissionDenied
        if actor.get("role") != "teacher":
            raise PermissionDenied("仅任课教师可以查看授权自习汇总")
        self._sharing_scope(actor, course_id, class_id, student_id)
        with self.engine.connect() as connection:
            grants = [dict(r) for r in connection.execute(text("""SELECT * FROM study_sharing_grants
                WHERE student_id=:uid AND course_id=:course AND class_id=:class_id AND revoked_at IS NULL"""),
                {"uid": student_id, "course": course_id, "class_id": class_id}).mappings()]
            rows = connection.execute(text("SELECT data FROM study_room_records WHERE student_id=:uid"), {"uid": student_id}).all()
        allowed = {g["grant_id"]: g for g in grants}
        records = []
        for row in rows:
            try:
                record = json.loads(row[0])
                grant = allowed.get(record.get("sharing_grant_id"))
                if not grant or not record.get("ended_at_utc"):
                    continue
                if record.get("course_id") != course_id or record.get("class_id") != class_id:
                    continue
                if timestamp(record["started_at_utc"]) < timestamp(grant["granted_at"]):
                    continue
                if timestamp(start_at) <= timestamp(record["ended_at_utc"]) < timestamp(end_at):
                    records.append(record)
            except (ValueError, TypeError, KeyError):
                continue
        valid = sum(r.get("valid_sample_seconds", 0) for r in records)
        focused = sum(r.get("focused_sample_seconds", 0) for r in records)
        total = sum(r.get("elapsed_seconds", 0) for r in records)
        source_version = hashlib.sha256(json.dumps({"grants": sorted(allowed),
            "records": sorted(records, key=lambda r: r["session_id"])}, sort_keys=True).encode()).hexdigest()
        return {"authorized": bool(grants), "sessions": len(records), "duration_seconds": round(total, 1),
                "source_version": source_version,
                "valid_sample_seconds": round(valid, 1), "unobserved_seconds": round(max(0, total-valid), 1),
                "focus_reference": round(100*focused/valid, 1) if valid >= 60 else None,
                "minimum_sample_seconds": 60,
                "coverage_percent": round(100*valid/total, 1) if total > 0 else None,
                "note": "仅为有效采样期间的专注参考，不代表学习态度、人格或心理状态"}

    @staticmethod
    def _now_label() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _base_status(self) -> dict[str, Any]:
        return {
            "status": "等待开始", "score": 0.0, "average_score": 0.0,
            "study_time": 0.0, "distract_time": 0.0, "away_time": 0.0,
            "focus": 0.0, "learning": False, "session_id": None,
            "start_time": "", "update_time": self._now_label(), "score_level": "未评分",
            "evaluation": "等待开始学习", "stability": 0.0, "presence_rate": 0.0,
            "camera_available": False, "dependency_ready": True, "mode": "browser",
            "client_camera": True,
            "ai_enabled": True, "telemetry_seen": False,
            "warning": "摄像头画面只在当前浏览器显示，仅上传识别后的统计信号。",
        }

    def _session_status(self, session: dict[str, Any]) -> dict[str, Any]:
        metrics: StudyMetrics = session["metrics"]
        metrics.advance()
        return {
            **self._base_status(), **metrics.snapshot(),
            "learning": True, "session_id": session["session_id"],
            "start_time": session["start_time"], "mode": "browser", "ai_mode": "local-model",
            "client_camera": True,
            "warning": "摄像头画面只在当前浏览器显示，仅上传识别后的统计信号。",
            "course_id": session.get("course_id"), "class_id": session.get("class_id"),
            "sharing_grant_id": session.get("sharing_grant_id"),
        }

    def status(self, student_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(student_id)
            if not session:
                return dict(self._completed.get(student_id) or self._base_status())
            return self._session_status(session)

    def start(self, student_id: str, course_id: str | None = None, class_id: str | None = None) -> dict[str, Any]:
        from class_task_service import stamp, utc_now
        from campus_service import ValidationError
        with self._lock:
            existing = self._sessions.get(student_id)
            if existing and (existing.get("course_id"), existing.get("class_id")) != (course_id, class_id):
                raise ValidationError("已有进行中的自习，请先结束后再改变课程共享范围")
            if student_id not in self._sessions:
                grant_id = None
                if course_id or class_id:
                    if not course_id or not class_id:
                        raise ValidationError("共享自习必须同时选择课程和班级")
                    actor = {"user_id": student_id, "role": "student"}
                    self._sharing_scope(actor, course_id, class_id)
                    grant = next((g for g in self.grants(actor) if g["course_id"] == course_id and g["class_id"] == class_id), None)
                    if not grant:
                        raise ValidationError("请先主动授权该课程及班级的自习共享")
                    grant_id = grant["grant_id"]
                self._sessions[student_id] = {
                    "session_id": uuid4().hex,
                    "start_time": self._now_label(),
                    "started_monotonic": time.monotonic(),
                    "metrics": StudyMetrics(),
                    "sharing_grant_id": grant_id, "course_id": course_id, "class_id": class_id,
                    "started_at_utc": stamp(utc_now()), "sample_last": None,
                    "sample_valid": False, "sample_focused": False,
                    "valid_sample_seconds": 0.0, "focused_sample_seconds": 0.0,
                }
        return self.status(student_id)

    def telemetry(self, student_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """接收浏览器端的脱敏识别信号，不接收图片、视频或本地路径。"""
        with self._lock:
            session = self._sessions.get(student_id)
            if not session:
                raise StudyRoomUnavailable("当前没有属于你的自习会话。")
            metrics: StudyMetrics = session["metrics"]
            sample_now = time.monotonic()
            # Only bounded intervals between two valid telemetry samples count.
            # No polling/status call or stale frame extrapolates focus across a disconnect.
            valid = bool(payload.get("camera_available", True)) and not payload.get("calibrating", False)
            previous = session["sample_last"]
            gap = sample_now - previous if previous is not None else 0
            if valid and session["sample_valid"] and 0 < gap <= 3:
                session["valid_sample_seconds"] += gap
                if session["sample_focused"]:
                    session["focused_sample_seconds"] += gap
            session["sample_last"] = sample_now
            session["sample_valid"] = valid
            session["sample_focused"] = bool(payload.get("face_ok") and payload.get("head_ok") and payload.get("person_ok") and not payload.get("eye_closed"))
            result = metrics.ingest(
                face_ok=bool(payload.get("face_ok", False)),
                head_ok=bool(payload.get("head_ok", False)),
                eye_closed=bool(payload.get("eye_closed", False)),
                person_ok=bool(payload.get("person_ok", False)),
                hand_near_face=bool(payload.get("hand_near_face", False)),
                hand_count=max(0, min(2, int(payload.get("hand_count", 0) or 0))),
                hand_confidence=max(0.0, min(1.0, float(payload.get("hand_confidence", 0.0) or 0.0))),
                head_score=max(0.0, min(1.0, float(payload.get("head_score", 0.0) or 0.0))),
                calibrating=bool(payload.get("calibrating", False)),
                camera_available=bool(payload.get("camera_available", True)),
            )
            return {
                **self._base_status(), **result,
                "learning": True, "session_id": session["session_id"],
                "start_time": session["start_time"], "mode": "browser", "ai_mode": "local-model",
                "client_camera": True,
                "course_id": session.get("course_id"), "class_id": session.get("class_id"),
                "sharing_grant_id": session.get("sharing_grant_id"),
                "warning": "摄像头画面只在当前浏览器显示，仅上传识别后的统计信号。",
            }

    def finish(self, student_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.pop(student_id, None)
            if not session:
                raise StudyRoomUnavailable("当前没有属于你的自习会话。")
            metrics: StudyMetrics = session["metrics"]
            metrics.advance()
            final_score = metrics.final_score()
            summary = metrics.snapshot()
            record = {
                **self._base_status(), **summary, "score": round(final_score, 1),
                "session_id": session["session_id"],
                "student_id": student_id, "date": datetime.now().strftime("%Y-%m-%d"),
                "start_time": session["start_time"], "end_time": self._now_label(),
                "status": "已完成", "learning": False,
                "score_level": summary["score_level"] if metrics.score_count == 0 else (
                    "优秀" if final_score >= 90 else "良好" if final_score >= 80 else "一般" if final_score >= 60 else "待提升"
                ),
                "evaluation": metrics.evaluation(final_score),
                "ai_frame_count": metrics.score_count,
                "score_model": "参考 AI 自习室 v3：帧评分50% + 专注度30% + 稳定性10% + 在场率10%",
                "warning": "",
            }
            from class_task_service import stamp, utc_now
            record.update({key: session[key] for key in ("sharing_grant_id", "course_id", "class_id", "started_at_utc", "valid_sample_seconds", "focused_sample_seconds")})
            record.update(ended_at_utc=stamp(utc_now()), elapsed_seconds=max(0, time.monotonic()-session["started_monotonic"]))
            with self.engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO study_room_records(student_id,data,created_at) VALUES(:student_id,:data,:created_at)"),
                    {"student_id": student_id, "data": json.dumps(record, ensure_ascii=False),
                     "created_at": datetime.now().isoformat()},
                )
            self._completed[student_id] = record
            self._invalidate_portraits(student_id)
            return dict(record)

    def records(self, student_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text("SELECT data FROM study_room_records WHERE student_id=:student_id ORDER BY id DESC LIMIT :limit"),
                {"student_id": student_id, "limit": max(1, min(int(limit), 1000))},
            ).all()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                result.append(json.loads(row[0]))
            except (TypeError, json.JSONDecodeError):
                continue
        return result

    def statistics(self, student_id: str) -> dict[str, Any]:
        records = self.records(student_id, 1000)
        if not records:
            return {"total_sessions": 0, "total_study_time": 0.0, "average_score": 0.0,
                    "average_focus": 0.0, "best_score": 0.0}
        scores = [float(item.get("score", 0) or 0) for item in records]
        focuses = [float(item.get("focus", 0) or 0) for item in records]
        return {
            "total_sessions": len(records),
            "total_study_time": round(sum(float(item.get("study_time", 0) or 0) for item in records), 1),
            "average_score": round(sum(scores) / len(scores), 1),
            "average_focus": round(sum(focuses) / len(focuses), 1),
            "best_score": round(max(scores), 1),
        }

    def clear_records(self, student_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM study_room_records WHERE student_id=:student_id"),
                {"student_id": student_id},
            )
        self._invalidate_portraits(student_id)
