from __future__ import annotations

import time

import pytest

from browser_study_room_service import BrowserStudyRoomService, StudyRoomUnavailable


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
