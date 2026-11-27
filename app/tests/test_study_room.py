from __future__ import annotations

import time

import pytest

from browser_study_room_service import BrowserStudyRoomService, StudyRoomUnavailable
from study_room_ai import StudyMetrics, StudyStateMachine, STATE_DISTRACT, STATE_STUDYING, frame_score


def test_browser_sessions_are_persisted_and_allow_multiple_students(tmp_path):
    service = BrowserStudyRoomService(tmp_path / "study-room.db")

    started = service.start("student-a")
    assert started["learning"] is True
    assert started["mode"] == "browser"
    assert service.start("student-b")["learning"] is True

    time.sleep(0.02)
    finished = service.finish("student-a")
    assert finished["status"] == "已完成"
    assert finished["study_time"] >= 0
    assert len(service.records("student-a")) == 1
    assert service.records("student-b") == []
    assert service.statistics("student-a")["total_sessions"] == 1
    with pytest.raises(StudyRoomUnavailable):
        service.finish("student-a")
    service.finish("student-b")


def test_clear_records_only_clears_the_current_student(tmp_path):
    service = BrowserStudyRoomService(tmp_path / "study-room.db")
    service.start("student-a")
    service.finish("student-a")
    service.start("student-b")
    service.finish("student-b")

    service.clear_records("student-a")
    assert service.records("student-a") == []
    assert len(service.records("student-b")) == 1


def test_reference_score_and_state_machine_rules_are_ported():
    assert frame_score(
        face_ok=True, head_ok=True, hand_count=0, raw_status=STATE_STUDYING,
        person_ok=True, head_score=1.0, hand_confidence=0.0,
    ) == 88.0
    assert frame_score(
        face_ok=True, head_ok=True, hand_count=0, raw_status=STATE_DISTRACT,
        person_ok=True, head_score=1.0, hand_confidence=0.0,
    ) == 39.6

    machine = StudyStateMachine()
    assert machine.update(face_ok=True, head_ok=True, person_ok=True, now=0.0) == "检测中"
    assert machine.update(face_ok=True, head_ok=True, person_ok=True, now=2.1) == STATE_STUDYING


def test_metrics_update_focus_realtime_and_final_score_without_zero_defaults():
    metrics = StudyMetrics()
    metrics.last_activity = 0.0
    metrics.ingest(
        face_ok=True, head_ok=True, eye_closed=False, person_ok=True,
        hand_near_face=False, hand_count=0, hand_confidence=0.0,
        head_score=1.0, calibrating=False, camera_available=True, now=0.0,
    )
    metrics.ingest(
        face_ok=True, head_ok=True, eye_closed=False, person_ok=True,
        hand_near_face=False, hand_count=0, hand_confidence=0.0,
        head_score=1.0, calibrating=False, camera_available=True, now=2.1,
    )
    metrics.ingest(
        face_ok=True, head_ok=True, eye_closed=False, person_ok=True,
        hand_near_face=False, hand_count=0, hand_confidence=0.0,
        head_score=1.0, calibrating=False, camera_available=True, now=3.1,
    )
    assert metrics.score_count == 3
    assert metrics.realtime_score(now=2.1) > 0
    assert metrics.focus() > 0
    assert metrics.final_score() > 0

def test_timer_only_and_sample_threshold(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("browser_study_room_service.time.monotonic", lambda: clock[0])
    room = BrowserStudyRoomService(tmp_path / "sampling.db")
    uid = "student"
    room.start(uid)
    clock[0] += 120
    assert room.telemetry(uid, {"camera_available": False})["focus"] is None
    saved = room.finish(uid)
    assert saved["score"] is None and saved["elapsed_seconds"] == 120
    assert room.records(uid)[0]["evaluation"] == "本次仅计时，未进行视觉状态评估"
    assert room.statistics(uid)["average_score"] is None

    room.start(uid)
    signal = dict(camera_available=True, face_ok=True, person_ok=True, head_ok=True)
    for _ in range(60):
        clock[0] += 1
        assert room.telemetry(uid, signal)["score"] is None
    clock[0] += 1
    assert room.telemetry(uid, signal)["score"] is not None
    clock[0] += 20
    assert room.status(uid)["score"] is None
    record = room.finish(uid)
    assert record["valid_sample_seconds"] == 60
    stats = room.statistics(uid)
    assert stats["evaluated_sessions"] == 1 and stats["total_sessions"] == 2
    assert stats["average_score"] == record["score"]
    assert stats["total_study_time"] == 201
