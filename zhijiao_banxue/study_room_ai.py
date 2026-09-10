from __future__ import annotations

import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


STATE_STUDYING = "正在学习"
STATE_DISTRACT = "注意分心"
STATE_AWAY = "离开"
STATE_DETECTING = "检测中"
STATE_SLEEP = "疑似睡觉"
STATE_CALIBRATING = "校准中"

SCORE_FACE = 35.0
SCORE_HEAD = 45.0
SCORE_HAND = 10.0
SCORE_HAND_FALLBACK = 8.0
SCORE_HAND_NEAR_FACE_FACTOR = 0.70
SCORE_DISTRACT_FACTOR = 0.45
SCORE_SLEEP_FACTOR = 0.20
SCORE_DETECTING_FACTOR = 0.80
REALTIME_WINDOW_SECONDS = 120.0


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def frame_score(
    *,
    face_ok: bool,
    head_ok: bool,
    hand_count: int,
    raw_status: str,
    person_ok: bool = True,
    head_score: float | None = None,
    hand_confidence: float = 1.0,
    hand_near_face: bool = False,
) -> float:
    """移植参考实现的单帧行为评分，范围为 0~100。"""

    if not person_ok or raw_status == STATE_AWAY:
        return 0.0

    soft_head_score = clamp01(1.0 if head_ok else 0.0) if head_score is None else clamp01(head_score)
    score = SCORE_FACE if face_ok else 0.0
    if face_ok:
        score += SCORE_HEAD * soft_head_score

    if hand_count > 0 and not hand_near_face:
        score += SCORE_HAND * clamp01(hand_confidence)
    elif face_ok and head_ok and not hand_near_face:
        score += SCORE_HAND_FALLBACK

    if hand_near_face:
        score *= SCORE_HAND_NEAR_FACE_FACTOR

    if raw_status == STATE_DISTRACT:
        score *= SCORE_DISTRACT_FACTOR
    elif raw_status == STATE_SLEEP:
        score *= SCORE_SLEEP_FACTOR
    elif raw_status == STATE_DETECTING:
        score *= SCORE_DETECTING_FACTOR

    return max(0.0, min(100.0, score))


class StudyStateMachine:
    """参考实现的迟滞状态机，避免单帧抖动导致状态闪烁。"""

    def __init__(
        self,
        away_confirm: float = 3.0,
        distract_confirm: float = 5.0,
        study_confirm: float = 2.0,
        sleep_confirm: float = 3.0,
    ) -> None:
        self.away_confirm = away_confirm
        self.distract_confirm = distract_confirm
        self.study_confirm = study_confirm
        self.sleep_confirm = sleep_confirm
        self.state = STATE_DETECTING
        self._signal: str | None = None
        self._signal_since = time.monotonic()

    @staticmethod
    def _classify(
        face_ok: bool,
        head_ok: bool,
        eye_closed: bool,
        person_ok: bool,
        hand_near_face: bool,
    ) -> str:
        if not face_ok:
            return "back" if person_ok else "none"
        if eye_closed:
            return "sleep"
        if hand_near_face or not head_ok:
            return "bad"
        return "good"

    def update(
        self,
        *,
        face_ok: bool,
        head_ok: bool,
        eye_closed: bool = False,
        person_ok: bool = True,
        hand_near_face: bool = False,
        now: float | None = None,
    ) -> str:
        current = time.monotonic() if now is None else now
        signal = self._classify(face_ok, head_ok, eye_closed, person_ok, hand_near_face)
        if signal != self._signal:
            self._signal = signal
            self._signal_since = current
        held = max(0.0, current - self._signal_since)
        state = self.state

        if state in (STATE_DETECTING, STATE_STUDYING):
            if signal == "none" and held >= self.away_confirm:
                state = STATE_AWAY
            elif signal == "sleep" and held >= self.sleep_confirm:
                state = STATE_SLEEP
            elif signal in ("bad", "back") and held >= self.distract_confirm:
                state = STATE_DISTRACT
            elif state == STATE_DETECTING and signal == "good" and held >= self.study_confirm:
                state = STATE_STUDYING
        elif state == STATE_DISTRACT:
            if signal == "good" and held >= self.study_confirm:
                state = STATE_STUDYING
            elif signal == "sleep" and held >= self.sleep_confirm:
                state = STATE_SLEEP
            elif signal == "none" and held >= self.away_confirm:
                state = STATE_AWAY
        elif state == STATE_SLEEP:
            if signal == "good" and held >= self.study_confirm:
                state = STATE_STUDYING
            elif signal in ("bad", "back") and held >= self.study_confirm:
                state = STATE_DISTRACT
            elif signal == "none" and held >= self.away_confirm:
                state = STATE_AWAY
        elif state == STATE_AWAY and signal != "none":
            state = STATE_DETECTING
            self._signal = None
            self._signal_since = current

        self.state = state
        return state

    def reset(self) -> None:
        self.state = STATE_DETECTING
        self._signal = None
        self._signal_since = time.monotonic()


@dataclass
class StudyMetrics:
    status: str = STATE_DETECTING
    study_time: float = 0.0
    distract_time: float = 0.0
    away_time: float = 0.0
    score_total: float = 0.0
    score_count: int = 0
    score_history: deque[float] = field(default_factory=lambda: deque(maxlen=600))
    score_window: deque[tuple[float, float]] = field(default_factory=deque)
    state_machine: StudyStateMachine = field(default_factory=StudyStateMachine)
    last_activity: float = field(default_factory=time.monotonic)
    telemetry_seen: bool = False
    camera_available: bool = False
    calibration_active: bool = False

    def _prune_window(self, now: float) -> None:
        cutoff = now - REALTIME_WINDOW_SECONDS
        while self.score_window and self.score_window[0][0] < cutoff:
            self.score_window.popleft()

    def _add_time(self, status: str, delta: float) -> None:
        if delta <= 0:
            return
        if status == STATE_STUDYING:
            self.study_time += delta
        elif status in (STATE_DISTRACT, STATE_SLEEP):
            self.distract_time += delta
        elif status == STATE_AWAY:
            self.away_time += delta

    def advance(self, now: float | None = None) -> float:
        current = time.monotonic() if now is None else now
        delta = max(0.0, current - self.last_activity)
        self.last_activity = current
        # 与参考 ai_server.py 一致，避免轮询中断造成一次性大幅跳变。
        self._add_time(self.status, min(delta, 1.0))
        self._prune_window(current)
        return delta

    def set_fallback_timer(self, now: float | None = None) -> None:
        self.advance(now)
        self.status = STATE_STUDYING
        self.telemetry_seen = True
        self.camera_available = False

    def ingest(
        self,
        *,
        face_ok: bool,
        head_ok: bool,
        eye_closed: bool,
        person_ok: bool,
        hand_near_face: bool,
        hand_count: int,
        hand_confidence: float,
        head_score: float,
        calibrating: bool,
        camera_available: bool,
        now: float | None = None,
    ) -> dict[str, Any]:
        current = time.monotonic() if now is None else now
        self.advance(current)
        self.telemetry_seen = True
        self.camera_available = camera_available

        if not camera_available:
            self.set_fallback_timer(current)
            return self.snapshot(current)

        self.calibration_active = calibrating
        if calibrating:
            self.status = STATE_CALIBRATING
            return self.snapshot(current)

        self.status = self.state_machine.update(
            face_ok=face_ok,
            head_ok=head_ok,
            eye_closed=eye_closed,
            person_ok=person_ok,
            hand_near_face=hand_near_face,
            now=current,
        )
        raw_status = STATE_AWAY if not person_ok else (
            STATE_SLEEP if eye_closed else (
                STATE_DISTRACT if hand_near_face or not face_ok or not head_ok else STATE_STUDYING
            )
        )
        score = frame_score(
            face_ok=face_ok,
            head_ok=head_ok,
            hand_count=hand_count,
            raw_status=raw_status,
            person_ok=person_ok,
            head_score=head_score,
            hand_confidence=hand_confidence,
            hand_near_face=hand_near_face,
        )
        self.score_total += score
        self.score_count += 1
        self.score_history.append(score)
        self.score_window.append((current, score))
        self._prune_window(current)
        return self.snapshot(current)

    def focus(self) -> float:
        total = self.study_time + self.distract_time + self.away_time
        if total <= 0:
            return 0.0
        return max(0.0, min(100.0, self.study_time / total * 100.0))

    def presence_rate(self) -> float:
        total = self.study_time + self.distract_time
        if total <= 0:
            return 0.0
        return max(0.0, min(100.0, self.study_time / total * 100.0))

    def stability(self) -> float:
        if len(self.score_history) < 10:
            return 100.0
        try:
            deviation = statistics.pstdev(self.score_history)
        except statistics.StatisticsError:
            return 100.0
        return max(0.0, min(100.0, 100.0 - deviation * 2.5))

    def realtime_score(self, now: float | None = None) -> float:
        current = time.monotonic() if now is None else now
        self._prune_window(current)
        if not self.score_window:
            return 0.0
        return sum(score for _, score in self.score_window) / len(self.score_window)

    def final_score(self) -> float:
        if self.score_count <= 0:
            return 0.0
        score = (
            (self.score_total / self.score_count) * 0.50
            + self.focus() * 0.30
            + self.stability() * 0.10
            + self.presence_rate() * 0.10
        )
        return max(0.0, min(100.0, score))

    def evaluation(self, score: float | None = None) -> str:
        current_score = self.realtime_score() if score is None else score
        total = self.study_time + self.distract_time + self.away_time
        if total <= 0:
            return "等待开始学习"
        if current_score >= 90 and self.focus() >= 90:
            return "学习状态非常好，请继续保持"
        if current_score >= 80 and self.focus() >= 80:
            return "学习状态良好，专注度较高"
        if current_score >= 70 and self.focus() >= 70:
            return "学习状态一般，可以进一步提高专注度"
        if self.away_time > self.study_time * 0.3:
            return "离开时间较多，建议减少离开"
        if self.distract_time > self.study_time * 0.3:
            return "分心时间较多，建议集中注意力"
        if self.focus() < 60:
            return "专注度偏低，建议减少无关活动"
        return "学习状态需要进一步改善"

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        current = time.monotonic() if now is None else now
        self._prune_window(current)
        realtime = self.realtime_score(current)
        return {
            "status": self.status,
            "score": round(realtime, 1),
            "average_score": round(self.score_total / self.score_count, 1) if self.score_count else 0.0,
            "study_time": round(self.study_time, 1),
            "distract_time": round(self.distract_time, 1),
            "away_time": round(self.away_time, 1),
            "focus": round(self.focus(), 1),
            "stability": round(self.stability(), 1),
            "presence_rate": round(self.presence_rate(), 1),
            "score_level": score_level(realtime),
            "evaluation": self.evaluation(realtime),
            "camera_available": self.camera_available,
            "telemetry_seen": self.telemetry_seen,
        }


def score_level(score: float) -> str:
    if score <= 0:
        return "未评分"
    if score >= 90:
        return "优秀"
    if score >= 80:
        return "良好"
    if score >= 60:
        return "一般"
    return "待提升"
