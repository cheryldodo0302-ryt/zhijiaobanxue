from pathlib import Path

import pytest

from auth_service import AuthService
from campus_service import CampusService, PermissionDenied
from database import LearningDatabase
from teacher_service import TeacherService


@pytest.fixture()
def services(tmp_path: Path):
    db = LearningDatabase(tmp_path / "teacher.db")
    campus = CampusService(db, tmp_path / "uploads", provider_factory=lambda: None)
    auth = AuthService(db, tmp_path / "secret")
    teachers = TeacherService(db, campus)
    return db, auth, teachers


def test_teacher_auth_refresh_and_revocation(services):
    _, auth, _ = services
    created = auth.create_user("teacher@example.edu", "safe-password-123", "teacher", "王老师")
    assert created["role"] == "teacher"
    user, access, refresh = auth.login("teacher@example.edu", "safe-password-123")
    assert auth.authenticate(access)["user_id"] == user["user_id"]
    refreshed_user, next_access, next_refresh = auth.refresh(refresh)
    assert refreshed_user["user_id"] == user["user_id"]
    assert auth.authenticate(next_access)["role"] == "teacher"
    with pytest.raises(PermissionDenied):
        auth.refresh(refresh)
    auth.revoke(next_refresh)
    with pytest.raises(PermissionDenied):
        auth.refresh(next_refresh)


def test_teacher_course_class_scope_and_membership(services, monkeypatch):
    monkeypatch.setenv("ZHIJIAO_STUDENT_DEFAULT_PASSWORD", "initial-password-123")
    _, auth, teachers = services
    teacher = auth.create_user("teacher-a", "safe-password-123", "teacher")
    other = auth.create_user("teacher-b", "safe-password-456", "teacher")
    course = teachers.create_course(teacher, "数据库原理")
    term = teachers.create_term(teacher, "2026 秋季")
    class_row = teachers.create_class(teacher, course["course_id"], term["term_id"], "临床一班")
    imported = teachers.add_members(teacher, class_row["class_id"], ["student_1", "student_2", "student_1"])
    assert imported["imported"] == 2
    assert len(imported["members"]) == 2
    assert imported["members"][0]["anonymous_id"]
    with pytest.raises(PermissionDenied):
        teachers.list_members(other, class_row["class_id"])


def test_legacy_shared_course_is_backfilled(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    db = LearningDatabase(db_path)
    campus = CampusService(db, tmp_path / "uploads", provider_factory=lambda: None)
    course = campus.create_course("共享课", "shared_course", "legacy_teacher", "teacher")
    campus.enroll_student(course["course_id"], "legacy_teacher", "legacy_student")
    LearningDatabase(db_path)
    class_row = db.fetch_one("SELECT * FROM classes WHERE course_id=?", (course["course_id"],))
    assert class_row is not None
    member = db.fetch_one(
        "SELECT * FROM class_memberships WHERE class_id=? AND student_id='legacy_student'",
        (class_row["class_id"],),
    )
    assert member is not None
