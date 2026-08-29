from __future__ import annotations

import time

import pytest

from study_room_service import StudyRoomBusy, StudyRoomService, StudyRoomUnavailable


def test_timer_fallback_is_persisted_per_student(tmp_path, monkeypatch):
    service = StudyRoomService(tmp_path / "study-room.db")
    monkeypatch.setattr(service, "_try_open_camera", lambda: False)

    started = service.start("student-a")
    assert started["learning"] is True
    assert started["mode"] == "timer"
    assert started["camera_available"] is False
    assert service.status("student-b")["busy"] is True
    with pytest.raises(StudyRoomBusy):
        service.start("student-b")

    time.sleep(0.02)
    finished = service.finish("student-a")
    assert finished["status"] == "已完成"
    assert finished["study_time"] >= 0
    assert len(service.records("student-a")) == 1
    assert service.records("student-b") == []
    assert service.statistics("student-a")["total_sessions"] == 1
    with pytest.raises(StudyRoomUnavailable):
        service.finish("student-b")


def test_clear_records_only_clears_the_current_student(tmp_path, monkeypatch):
    service = StudyRoomService(tmp_path / "study-room.db")
    monkeypatch.setattr(service, "_try_open_camera", lambda: False)
    service.start("student-a")
    service.finish("student-a")
    service.start("student-b")
    service.finish("student-b")

    service.clear_records("student-a")
    assert service.records("student-a") == []
    assert len(service.records("student-b")) == 1
