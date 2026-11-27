import json
import shutil

from auth_service import AuthService
from campus_service import CampusService
from config import teacher_portal_enabled_for
from database import LearningDatabase
from ingestion_service import IngestionService
from scripts.seed_showcase import seed, TEACHER_ID
from teacher_service import TeacherService
from teaching_archive_service import TeachingArchiveService


def test_showcase_accounts_archives_and_source_previews_survive_folder_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("ZHIJIAO_AI_MODE", "mock")
    original = tmp_path / "first" / "data"
    db = LearningDatabase(original / "learning.db")
    result = seed(db, with_learning=False)
    before = {table: db.fetch_one(f"SELECT COUNT(*) n FROM {table}")["n"]
              for table in ("users", "courses", "classes", "teaching_archive_items", "course_documents")}
    assert seed(db, with_learning=False) == result
    for table, count in before.items():
        assert db.fetch_one(f"SELECT COUNT(*) n FROM {table}")["n"] == count
    db.engine.dispose()
    moved = tmp_path / "another-computer" / "中文目录" / "data"
    shutil.copytree(original, moved)
    db = LearningDatabase(moved / "learning.db")
    credentials = json.loads((moved / "showcase" / "accounts.json").read_text())
    auth = AuthService(db)
    teacher = auth.login(credentials["username"], credentials["password"])[0]
    assert teacher["user_id"] == TEACHER_ID
    assert teacher_portal_enabled_for(teacher)
    campus = CampusService(db, moved / "uploads")
    teachers = TeacherService(db, campus)
    ingestion = IngestionService(db, campus)
    archives = TeachingArchiveService(db, campus, ingestion)
    student = auth.login("2026240101", "2026240101")[0]
    assert len(teachers.list_courses(teacher)) == 3
    assert len(teachers.list_classes(teacher)) == 6
    assert db.fetch_one("SELECT COUNT(*) n FROM users WHERE role='student'")["n"] == 60
    for course in teachers.list_courses(teacher):
        workbench = archives.workbench(teacher, course["course_id"])
        assert workbench["metrics"]["completeness"] == 100
        assert workbench["metrics"]["class_coverage"] == 100
        assert workbench["metrics"]["hours_consistent"]
        assert workbench["metrics"]["review_required"] == 0
        for document in workbench["documents"]:
            media, content = archives.preview_content(teacher, document["archive_document_id"])
            assert content
            _, path = archives.public_document(teacher, document["archive_document_id"])
            assert path.is_relative_to(moved)
        sources = ingestion.list_student_source_files(student, course["course_id"])
        assert sources
        assert ingestion.preview_file(student, sources[0]["document_id"])[1]
    db.engine.dispose()
