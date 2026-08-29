from __future__ import annotations

import json
import os
import sqlite3
import statistics
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from config import DATA_DIR


class StudyRoomUnavailable(RuntimeError):
    """Raised when a camera/vision operation cannot be started."""


class StudyRoomBusy(RuntimeError):
    """Raised when another student currently owns the local camera."""


class StudyRoomService:
    """Single-camera AI study room with a timer-only fallback.

    The source study-room implementation is intentionally kept as the vision
    engine in ``student_study_room/study_judge.py``.  This adapter provides the
    main system's student identity, REST-friendly state, per-student history,
    and a safe fallback when optional MediaPipe/OpenCV packages or a camera are
    unavailable.
    """

    REALTIME_WINDOW = 120.0
    _lock = threading.RLock()

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(
            db_path
            or os.environ.get("ZHIJIAO_STUDY_ROOM_DB", "")
            or (DATA_DIR / "study_room.db")
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._active_student: str | None = None
        self._last_completed_student: str | None = None
        self._stream_tokens: dict[str, tuple[str, float]] = {}
        self._session_id: str | None = None
        self._learning = False
        self._mode = "timer"
        self._camera_available = False
        self._dependency_ready = False
        self._warning = ""
        self._cv2: Any = None
        self._judge: Any = None
        self._cap: Any = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._latest_frame: bytes | None = None
        self._state_machine: Any = None
        self._calibrator: Any = None
        self._last_time = time.time()
        self._study_time = 0.0
        self._distract_time = 0.0
        self._away_time = 0.0
        self._score_total = 0.0
        self._score_count = 0
        self._score_history: list[float] = []
        self._score_window: deque[tuple[float, float]] = deque()
        self._last_status: dict[str, Any] = self._base_status()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS study_room_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )

    @staticmethod
    def _now_label() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _base_status(self) -> dict[str, Any]:
        return {
            "status": "等待开始",
            "score": 0.0,
            "average_score": 0.0,
            "study_time": 0.0,
            "distract_time": 0.0,
            "away_time": 0.0,
            "focus": 0.0,
            "learning": False,
            "session_id": None,
            "start_time": "",
            "update_time": self._now_label(),
            "score_level": "未开始",
            "evaluation": "等待开始学习",
            "stability": 0.0,
            "presence_rate": 0.0,
            "camera_available": False,
            "dependency_ready": False,
            "mode": "timer",
            "warning": "",
        }

    def _load_runtime(self) -> None:
        if self._dependency_ready or self._warning:
            return
        try:
            import cv2  # type: ignore

            from student_study_room import study_judge  # type: ignore
        except Exception as exc:  # optional dependency/model failures
            self._warning = (
                "摄像头 AI 未启用：请安装 requirements-study-room.txt；"
                f"当前仍可使用计时自习（{type(exc).__name__}）。"
            )
            return
        self._cv2 = cv2
        self._judge = study_judge
        self._state_machine = study_judge.StudyStateMachine()
        self._dependency_ready = True
        self._warning = ""

    def _try_open_camera(self) -> bool:
        self._load_runtime()
        if not self._dependency_ready:
            return False
        try:
            backend = getattr(self._cv2, "CAP_DSHOW", 0)
            cap = self._cv2.VideoCapture(0, backend)
            if not cap.isOpened():
                cap.release()
                cap = self._cv2.VideoCapture(0)
            if not cap.isOpened():
                cap.release()
                self._warning = "未检测到可用摄像头，已切换为计时自习。"
                return False
            cap.set(self._cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(self._cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(self._cv2.CAP_PROP_FPS, 30)
            self._cap = cap
            self._camera_available = True
            self._mode = "vision"
            return True
        except Exception as exc:
            self._warning = f"摄像头初始化失败，已切换为计时自习：{exc}"
            self._camera_available = False
            return False

    @staticmethod
    def _score_level(score: float) -> str:
        if score >= 90:
            return "优秀"
        if score >= 80:
            return "良好"
        if score >= 70:
            return "一般"
        if score >= 60:
            return "较低"
        return "较差"

    def _focus(self) -> float:
        total = self._study_time + self._distract_time + self._away_time
        return max(0.0, min(100.0, self._study_time / total * 100)) if total else 0.0

    def _presence_rate(self) -> float:
        total = self._study_time + self._distract_time
        return max(0.0, min(100.0, self._study_time / total * 100)) if total else 0.0

    def _stability(self) -> float:
        if len(self._score_history) < 10:
            return 100.0 if self._score_count else 0.0
        recent = self._score_history[-120:]
        return max(0.0, min(100.0, 100 - statistics.pstdev(recent) * 2.5))

    def _realtime_score(self) -> float:
        if not self._score_window:
            return 0.0
        return sum(score for _, score in self._score_window) / len(self._score_window)

    def _final_score(self) -> float:
        if not self._score_count:
            return 0.0
        frame_average = self._score_total / self._score_count
        return max(
            0.0,
            min(
                100.0,
                frame_average * 0.50
                + self._focus() * 0.30
                + self._stability() * 0.10
                + self._presence_rate() * 0.10,
            ),
        )

    def _evaluation(self, score: float) -> str:
        if self._mode == "timer":
            return "已记录自习时长；安装摄像头 AI 依赖后可获得行为评分。"
        focus = self._focus()
        if score >= 90 and focus >= 90:
            return "学习状态非常好，请继续保持"
        if score >= 80 and focus >= 80:
            return "学习状态良好，专注度较高"
        if score >= 70 and focus >= 70:
            return "学习状态一般，可以进一步提高专注度"
        if self._away_time > self._study_time * 0.3:
            return "离开时间较多，建议减少离开"
        return "建议保持正脸并减少分心行为"

    def _refresh_timer(self) -> None:
        if not self._learning:
            return
        now = time.time()
        delta = max(0.0, min(1.0, now - self._last_time))
        self._last_time = now
        if self._mode == "timer":
            self._study_time += delta

    def _status_locked(self, student_id: str) -> dict[str, Any]:
        if self._active_student and self._active_student != student_id:
            return {
                **self._base_status(),
                "busy": True,
                "warning": "本机摄像头正在被另一位学生使用。",
            }
        if not self._active_student and self._last_completed_student not in (None, student_id):
            return self._base_status()
        self._refresh_timer()
        if self._mode == "timer":
            score = 0.0
            status = "计时中（未启用摄像头）" if self._learning else self._last_status.get("status", "等待开始")
        else:
            score = self._realtime_score() if self._learning else self._last_status.get("score", 0.0)
            status = self._state_machine.state if self._state_machine is not None else "检测中"
        data = {
            **self._last_status,
            "status": status,
            "score": round(score, 1),
            "average_score": round(self._score_total / self._score_count, 1) if self._score_count else 0.0,
            "study_time": round(self._study_time, 1),
            "distract_time": round(self._distract_time, 1),
            "away_time": round(self._away_time, 1),
            "focus": round(self._focus(), 1),
            "learning": self._learning,
            "session_id": self._session_id,
            "camera_available": self._camera_available,
            "dependency_ready": self._dependency_ready,
            "mode": self._mode,
            "warning": self._warning,
            "stability": round(self._stability(), 1),
            "presence_rate": round(self._presence_rate(), 1),
            "update_time": self._now_label(),
        }
        self._last_status = data
        return dict(data)

    def status(self, student_id: str) -> dict[str, Any]:
        with self._lock:
            return self._status_locked(student_id)

    def start(self, student_id: str) -> dict[str, Any]:
        with self._lock:
            if self._learning:
                if self._active_student != student_id:
                    raise StudyRoomBusy("本机摄像头正在被另一位学生使用。")
                return self._status_locked(student_id)
            self._active_student = student_id
            self._session_id = uuid4().hex
            self._learning = True
            self._mode = "timer"
            self._camera_available = False
            self._dependency_ready = False
            self._warning = ""
            self._cap = None
            self._latest_frame = None
            self._stop.clear()
            self._last_time = time.time()
            self._study_time = self._distract_time = self._away_time = 0.0
            self._score_total = 0.0
            self._score_count = 0
            self._score_history = []
            self._score_window.clear()
            self._calibrator = None
            camera = self._try_open_camera()
            if camera:
                self._state_machine.reset()
                self._judge.clear_calibration()
                self._calibrator = self._judge.Calibrator()
                self._thread = threading.Thread(target=self._camera_loop, name="study-room-camera", daemon=True)
                self._thread.start()
            self._last_status = {
                **self._base_status(),
                "status": "校准中" if camera else "计时中（未启用摄像头）",
                "learning": True,
                "session_id": self._session_id,
                "start_time": self._now_label(),
                "camera_available": camera,
                "dependency_ready": self._dependency_ready,
                "mode": self._mode,
                "warning": self._warning,
            }
            return self._status_locked(student_id)

    def _camera_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                cap = self._cap
            if cap is None:
                return
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.1)
                continue
            try:
                processed = self._process_frame(frame)
                ok, encoded = self._cv2.imencode(".jpg", processed)
                if ok:
                    with self._lock:
                        self._latest_frame = encoded.tobytes()
            except Exception as exc:
                with self._lock:
                    self._warning = f"摄像头 AI 分析异常，已保留计时：{exc}"
                time.sleep(0.1)

    def _process_frame(self, frame: Any) -> Any:
        with self._lock:
            now = time.time()
            delta = max(0.0, min(1.0, now - self._last_time))
            self._last_time = now
            frame = self._cv2.flip(frame, 1)
            result = self._judge.analyze_frame(frame)
            status = self._state_machine.update(
                result["face_ok"], result["head_ok"], result["eye_closed"],
                result["person_ok"], result["hand_near_face"], now,
            )
            if self._calibrator is not None:
                calibration = self._calibrator.add(
                    result["yaw"], result["pitch"], result["roll_deg"], result["face_ok"], now,
                )
                if calibration == "timeout":
                    self._judge.clear_calibration()
                    self._calibrator = None
                elif calibration is not None:
                    self._calibrator = None
                else:
                    status = self._judge.STATE_CALIBRATING
            if self._calibrator is None:
                if status == self._judge.STATE_STUDYING:
                    self._study_time += delta
                elif status in (self._judge.STATE_DISTRACT, self._judge.STATE_SLEEP):
                    self._distract_time += delta
                elif status == self._judge.STATE_AWAY:
                    self._away_time += delta
                score = float(result.get("score", 0.0))
                self._score_total += score
                self._score_count += 1
                self._score_history.append(score)
                self._score_history = self._score_history[-300:]
                self._score_window.append((now, score))
                while self._score_window and self._score_window[0][0] < now - self.REALTIME_WINDOW:
                    self._score_window.popleft()
            realtime = self._realtime_score()
            self._last_status = {
                **self._last_status,
                "status": status,
                "score": round(realtime, 1),
                "average_score": round(self._score_total / self._score_count, 1) if self._score_count else 0.0,
                "study_time": round(self._study_time, 1),
                "distract_time": round(self._distract_time, 1),
                "away_time": round(self._away_time, 1),
                "focus": round(self._focus(), 1),
                "stability": round(self._stability(), 1),
                "presence_rate": round(self._presence_rate(), 1),
                "learning": self._learning,
                "session_id": self._session_id,
                "camera_available": True,
                "dependency_ready": True,
                "mode": "vision",
                "update_time": self._now_label(),
            }
            for label, value, y in (("Status", status, 40), ("Score", realtime, 80), ("Focus", self._focus(), 120)):
                self._cv2.putText(frame, f"{label}: {value:.1f}" if isinstance(value, float) else f"{label}: {value}", (20, y), self._cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 220, 120), 2)
            return frame

    def finish(self, student_id: str) -> dict[str, Any]:
        with self._lock:
            if self._active_student != student_id:
                raise StudyRoomUnavailable("当前没有属于你的自习会话。")
            if not self._learning:
                return self._last_status
            self._refresh_timer()
            final_score = self._final_score()
            record = {
                "session_id": self._session_id,
                "student_id": student_id,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "start_time": self._last_status.get("start_time", ""),
                "end_time": self._now_label(),
                "status": "已完成",
                "score": round(final_score, 1),
                "study_time": round(self._study_time, 1),
                "distract_time": round(self._distract_time, 1),
                "away_time": round(self._away_time, 1),
                "focus": round(self._focus(), 1),
                "stability": round(self._stability(), 1),
                "presence_rate": round(self._presence_rate(), 1),
                "mode": self._mode,
            }
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO study_room_records(student_id,data,created_at) VALUES(?,?,?)",
                    (student_id, json.dumps(record, ensure_ascii=False), datetime.now().isoformat()),
                )
            self._stop.set()
            thread = self._thread
            cap = self._cap
            self._thread = None
            self._cap = None
            self._learning = False
            self._active_student = None
            self._last_completed_student = student_id
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            self._last_status = {
                **self._last_status,
                **record,
                "status": "已完成",
                "learning": False,
                "session_id": record["session_id"],
                "score_level": self._score_level(final_score),
                "evaluation": self._evaluation(final_score),
                "camera_available": bool(self._camera_available),
                "dependency_ready": bool(self._dependency_ready),
                "warning": self._warning,
                "update_time": self._now_label(),
            }
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        return dict(self._last_status)

    def records(self, student_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT data FROM study_room_records WHERE student_id=? ORDER BY id DESC LIMIT ?",
                (student_id, max(1, min(limit, 100))),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for (payload,) in rows:
            try:
                result.append(json.loads(payload))
            except (TypeError, json.JSONDecodeError):
                continue
        return result

    def statistics(self, student_id: str) -> dict[str, Any]:
        records = self.records(student_id, 1000)
        if not records:
            return {"total_sessions": 0, "total_study_time": 0.0, "average_score": 0.0, "average_focus": 0.0, "best_score": 0.0}
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
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM study_room_records WHERE student_id=?", (student_id,))

    def video_stream(self, student_id: str) -> Iterator[bytes]:
        with self._lock:
            if self._active_student != student_id or not self._camera_available:
                raise StudyRoomUnavailable("当前没有可用的摄像头视频流。")
        while True:
            with self._lock:
                if self._active_student != student_id or not self._learning:
                    return
                frame = self._latest_frame
            if frame:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            time.sleep(0.05)

    def issue_stream_token(self, student_id: str, ttl: int = 300) -> str:
        with self._lock:
            if self._active_student != student_id or not self._camera_available:
                raise StudyRoomUnavailable("当前没有可用的摄像头视频流。")
            token = uuid4().hex
            self._stream_tokens[token] = (student_id, time.time() + max(30, min(ttl, 600)))
            return token

    def resolve_stream_token(self, token: str) -> str | None:
        with self._lock:
            entry = self._stream_tokens.get(token)
            if not entry:
                return None
            student_id, expires_at = entry
            if expires_at <= time.time():
                self._stream_tokens.pop(token, None)
                return None
            if self._active_student != student_id or not self._learning:
                return None
            return student_id


study_room = StudyRoomService()
