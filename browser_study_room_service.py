from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from config import DATA_DIR


class StudyRoomUnavailable(RuntimeError):
    """Raised when a student has no active browser study session."""


class BrowserStudyRoomService:
    """Per-student timer state; camera frames always remain in the browser."""

    def __init__(self, db_path: Path | None = None) -> None:
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
            "warning": "摄像头画面只在当前浏览器显示，不上传服务器。",
        }

    def status(self, student_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(student_id)
            if not session:
                return dict(self._completed.get(student_id) or self._base_status())
            elapsed = max(0.0, time.monotonic() - float(session["started_monotonic"]))
            return {
                **self._base_status(), "status": "专注计时中", "learning": True,
                "session_id": session["session_id"], "start_time": session["start_time"],
                "study_time": round(elapsed, 1),
            }

    def start(self, student_id: str) -> dict[str, Any]:
        with self._lock:
            if student_id not in self._sessions:
                self._sessions[student_id] = {
                    "session_id": uuid4().hex,
                    "start_time": self._now_label(),
                    "started_monotonic": time.monotonic(),
                }
        return self.status(student_id)

    def finish(self, student_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.pop(student_id, None)
            if not session:
                raise StudyRoomUnavailable("当前没有属于你的自习会话。")
            elapsed = max(0.0, time.monotonic() - float(session["started_monotonic"]))
            record = {
                **self._base_status(), "session_id": session["session_id"],
                "student_id": student_id, "date": datetime.now().strftime("%Y-%m-%d"),
                "start_time": session["start_time"], "end_time": self._now_label(),
                "status": "已完成", "study_time": round(elapsed, 1), "learning": False,
                "evaluation": "已记录本次专注时长；浏览器摄像头画面未上传。",
                "warning": "",
            }
            with self.engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO study_room_records(student_id,data,created_at) VALUES(:student_id,:data,:created_at)"),
                    {"student_id": student_id, "data": json.dumps(record, ensure_ascii=False),
                     "created_at": datetime.now().isoformat()},
                )
            self._completed[student_id] = record
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
