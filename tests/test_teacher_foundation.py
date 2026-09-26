from datetime import date
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
    created = auth.create_user("teacher@example.edu", "safe-password-123", "teacher", "测试教师")
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
    imported = teachers.add_members(teacher, class_row["class_id"], ["20260001", "20260002", "20260001"])
    assert imported["imported"] == 2
    assert len(imported["members"]) == 2
    assert imported["members"][0]["anonymous_id"]
    with pytest.raises(PermissionDenied):
        teachers.list_members(other, class_row["class_id"])


def test_teacher_can_update_and_delete_own_class(services, monkeypatch):
    monkeypatch.setenv("ZHIJIAO_STUDENT_DEFAULT_PASSWORD", "initial-password-123")
    db, auth, teachers = services
    teacher = auth.create_user("class-editor", "safe-password-123", "teacher")
    other = auth.create_user("class-editor-other", "safe-password-456", "teacher")
    course = teachers.create_course(teacher, "数据库原理")
    next_course = teachers.create_course(teacher, "数据结构")
    term = teachers.create_term(teacher, "2026 秋季")
    next_term = teachers.create_term(teacher, "2027 春季")
    class_row = teachers.create_class(teacher, course["course_id"], term["term_id"], "信管一班")

    updated = teachers.update_class(teacher, class_row["class_id"], {
        "course_id": next_course["course_id"], "term_id": next_term["term_id"],
        "class_name": "信管强化班", "class_variant": "A班",
        "teaching_time_slot": "周一 1-2 节", "campus": "校区B",
        "cohort_year": "2025", "major": "医学信息工程", "teaching_level": "进阶",
    })
    assert updated["course_id"] == next_course["course_id"]
    assert updated["term_id"] == next_term["term_id"]
    assert updated["class_name"] == "信管强化班"
    assert updated["major"] == "医学信息工程"

    imported = teachers.add_members(teacher, class_row["class_id"], ["20260001"])
    assert imported["imported"] == 1
    with pytest.raises(PermissionDenied):
        teachers.update_class(other, class_row["class_id"], {"class_name": "越权修改"})

    teachers.delete_class(teacher, class_row["class_id"])
    assert db.fetch_one("SELECT * FROM classes WHERE class_id=?", (class_row["class_id"],)) is None
    assert db.fetch_one(
        "SELECT * FROM class_memberships WHERE class_id=?", (class_row["class_id"],)
    ) is None
    with pytest.raises(PermissionDenied):
        teachers.delete_class(other, class_row["class_id"])


def test_teacher_can_reset_only_own_class_student_password(services, monkeypatch):
    monkeypatch.setenv("ZHIJIAO_STUDENT_DEFAULT_PASSWORD", "initial-password-123")
    db, auth, teachers = services
    teacher = auth.create_user("reset-teacher", "safe-password-123", "teacher")
    other = auth.create_user("other-reset-teacher", "safe-password-456", "teacher")
    course = teachers.create_course(teacher, "数据库原理")
    term = teachers.create_term(teacher, "2026 秋季")
    class_row = teachers.create_class(teacher, course["course_id"], term["term_id"], "信管一班")
    imported = teachers.add_members(teacher, class_row["class_id"], ["20260001"])
    student_id = imported["members"][0]["user_id"]
    result = teachers.reset_student_password(
        teacher, class_row["class_id"], student_id, "temporary-password-456",
    )
    assert result["must_change_password"] == 1
    student, _access, _refresh = auth.login("20260001", "temporary-password-456")
    assert student["must_change_password"] == 1
    assert db.fetch_all("SELECT * FROM refresh_tokens WHERE user_id=?", (student_id,))
    with pytest.raises(PermissionDenied):
        teachers.reset_student_password(
            other, class_row["class_id"], student_id, "another-password-789",
        )


def test_teaching_year_period_and_class_variant_are_independent_dimensions(services):
    _, auth, teachers = services
    teacher = auth.create_user("teacher-dimensions", "safe-password-123", "teacher")
    course = teachers.create_course(teacher, "数据库原理")
    term = teachers.create_term(
        teacher, "2026-2027 秋季", academic_year="2026-2027", teaching_period="秋季学期"
    )
    main_campus = teachers.create_class(
        teacher, course["course_id"], term["term_id"], "数据库A班",
        "A班（校区A）", "周一 1-2 节",
    )
    campus_b = teachers.create_class(
        teacher, course["course_id"], term["term_id"], "数据库B班",
        "B班（校区B）", "周三 3-4 节",
    )
    assert term["academic_year"] == "2026-2027"
    assert term["teaching_period"] == "秋季学期"
    assert main_campus["class_variant"] == "A班（校区A）"
    assert campus_b["class_variant"] == "B班（校区B）"
    assert {row["teaching_time_slot"] for row in teachers.list_classes(teacher)} == {
        "周一 1-2 节", "周三 3-4 节",
    }


def test_institution_profile_merges_configuration_and_history(services, monkeypatch):
    _, auth, teachers = services
    teacher = auth.create_user("profile-teacher", "safe-password-123", "teacher")
    course = teachers.create_course(teacher, "数据库原理")
    term = teachers.create_term(teacher, "2026-2027 第一学期")
    teachers.create_class(
        teacher, course["course_id"], term["term_id"], "校区C班", campus="校区C",
        major="医学信息工程",
    )
    monkeypatch.setenv("ZHIJIAO_SCHOOL_NAME", "测试大学")
    monkeypatch.setenv("ZHIJIAO_SCHOOL_CAMPUSES", "校区A,校区B")
    monkeypatch.setenv("ZHIJIAO_SCHOOL_MAJORS", "信息管理与信息系统")
    profile = teachers.institution_profile(teacher)
    assert profile["school_name"] == "测试大学"
    assert profile["campuses"] == ["校区A", "校区B", "校区C"]
    assert profile["majors"] == ["信息管理与信息系统", "医学信息工程"]


def test_term_start_date_generates_real_weekly_class_dates(services):
    _, auth, teachers = services
    teacher = auth.create_user("calendar-teacher", "safe-password-123", "teacher")
    course = teachers.create_course(teacher, "数据库原理")
    term = teachers.create_term(teacher, "2026 秋季")
    term = teachers.update_term(teacher, term["term_id"], {
        "starts_on": date(2026, 9, 1), "ends_on": date(2026, 9, 30),
    })
    class_row = teachers.create_class(
        teacher, course["course_id"], term["term_id"], "信管一班"
    )
    teachers.replace_weekly_schedules(teacher, class_row["class_id"], [{
        "weekday": 3, "start_time": "08:00", "end_time": "09:40",
        "location": "教学楼 101", "starts_week": 1, "ends_week": 3,
    }])

    calendar = teachers.course_calendar(teacher, course["course_id"], term["term_id"])

    assert [event["date"] for event in calendar["events"]] == [
        "2026-09-02", "2026-09-09", "2026-09-16",
    ]
    assert calendar["events"][0]["location"] == "教学楼 101"


def test_legacy_shared_course_is_backfilled(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    db = LearningDatabase(db_path)
    campus = CampusService(db, tmp_path / "uploads", provider_factory=lambda: None)
    course = campus.create_course("共享课", "shared_course", "legacy_teacher", "teacher")
    campus.enroll_student(course["course_id"], "legacy_teacher", "legacy_student")
    # Simulate a pre-upgrade database; new courses must not grow default classes on every restart.
    db.execute("DELETE FROM schema_migrations WHERE migration_id='legacy_class_backfill_once'")
    LearningDatabase(db_path)
    class_row = db.fetch_one("SELECT * FROM classes WHERE course_id=?", (course["course_id"],))
    assert class_row is not None
    member = db.fetch_one(
        "SELECT * FROM class_memberships WHERE class_id=? AND student_id='legacy_student'",
        (class_row["class_id"],),
    )
    assert member is not None
