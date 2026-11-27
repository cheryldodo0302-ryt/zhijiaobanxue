import json
import re
import shutil
from datetime import timedelta

import pytest

from auth_service import AuthService
from browser_study_room_service import BrowserStudyRoomService
from campus_service import CampusService, PermissionDenied
from class_task_service import ClassTaskService, stamp, timestamp
from database import LearningDatabase
from ingestion_service import IngestionService
from knowledge_graph_service import KnowledgeGraphService
from scripts.seed_showcase import seed, TEACHER_ID
from student_portrait_service import StudentPortraitService


def test_complete_showcase_is_anonymous_graded_idempotent_and_portable(tmp_path, monkeypatch):
    monkeypatch.setenv("ZHIJIAO_AI_MODE", "mock")
    original = tmp_path / "source" / "data"
    db = LearningDatabase(original / "learning.db")
    courses = seed(db)
    tables = ("users", "class_tasks", "class_task_submissions", "question_bank_items", "question_bank_attempts",
              "learning_events", "knowledge_graph_versions", "knowledge_graph_nodes", "student_portrait_evaluations")
    counts = {t: db.fetch_one(f"SELECT COUNT(*) n FROM {t}")["n"] for t in tables}
    assert counts["class_tasks"] == 84
    assert counts["question_bank_items"] == 72
    assert counts["knowledge_graph_nodes"] == 51
    assert counts["student_portrait_evaluations"] == 180
    password = db.fetch_one("SELECT password_hash FROM users WHERE username='2026240101'")["password_hash"]
    assert seed(db) == courses
    assert {t: db.fetch_one(f"SELECT COUNT(*) n FROM {t}")["n"] for t in tables} == counts
    assert db.fetch_one("SELECT password_hash FROM users WHERE username='2026240101'")["password_hash"] == password
    assert all(re.fullmatch(r"[\u4e00-\u9fff]\*\*", r["display_name"]) for r in db.fetch_all("SELECT display_name FROM users WHERE role='student'"))
    assert "陈**" in (original / "showcase" / "学生账号名单.csv").read_text(encoding="utf-8-sig")
    assert "陈予安" not in (original / "showcase" / "学生账号名单.csv").read_text(encoding="utf-8-sig")
    db.engine.dispose()

    copied = tmp_path / "另一台电脑" / "data"
    shutil.copytree(original, copied)
    db = LearningDatabase(copied / "learning.db")
    campus = CampusService(db, copied / "uploads")
    auth = AuthService(db)
    teacher = auth.get_user(TEACHER_ID)
    student = auth.login("2026240101", "2026240101")[0]
    tasks = ClassTaskService(campus)
    study = BrowserStudyRoomService(copied / "study_room.db", campus=campus)
    portraits = StudentPortraitService(campus, study)
    try:
        for course in courses:
            course_id, class_id = course["course_id"], course["classes"][0]
            anchor = timestamp(course["learning"]["anchor"])
            period = [stamp(anchor-timedelta(days=29)), stamp(anchor+timedelta(days=1))]
            portrait = portraits.get(teacher, course_id, class_id, student["user_id"], *period)
            assert portrait["metrics"]["homework"]["count"] >= 3
            assert portrait["metrics"]["exam"]["count"] >= 3
            assert portrait["trends"]["homework"]["status"] == "available"
            assert portrait["trends"]["exam"]["status"] == "available"
            assert len(portrait["metrics"]["knowledge_points"]) == 8
            assert portrait["metrics"]["study"]["sessions"] == 3
            assert portrait["metrics"]["study"]["focus_reference"] is not None
            assert portrait["evaluation"]["status"] == "draft"
            assert portrait["evaluation"]["label"] == "演示模拟评价"
            assert "演示模拟评价" in portrait["evaluation"]["content"]["overview"][0]["text"]
            assert len(tasks.list_tasks(student, course_id, class_id)) == 14
            assert any(not task["submissions"] for task in tasks.list_tasks(student, course_id, class_id))
            graph = KnowledgeGraphService(db, campus).student_graph(student, course_id)
            assert len(graph["nodes"]) == 17 and len(graph["relations"]) == 25
            diagnosis = IngestionService(db, campus).teaching_overview(teacher, course_id, class_id=class_id)
            assert diagnosis["learning"]["active_students"] == 30
            assert diagnosis["learning"]["quiz_count"] > 0
            assert diagnosis["learning"]["question_count"] > 0
            assert diagnosis["learning"]["weak_points"] and diagnosis["priorities"]
            with pytest.raises(PermissionDenied):
                tasks.list_tasks(student, course_id, course["classes"][1])
        for row in db.fetch_all("SELECT stored_path FROM question_bank_imports UNION SELECT stored_path FROM knowledge_graph_import_files"):
            from pathlib import Path
            assert Path(row["stored_path"]).is_file() and Path(row["stored_path"]).is_relative_to(copied)
    finally:
        study.engine.dispose()
        db.engine.dispose()
