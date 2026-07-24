import io
import json

import pytest
from argon2 import PasswordHasher
from openpyxl import Workbook

from auth_service import AuthService
from campus_service import CampusService, PermissionDenied, ValidationError
from database import LearningDatabase
from ingestion_service import IngestionService
from teacher_service import TeacherService


def teacher_scope(tmp_path):
    db = LearningDatabase(tmp_path / "scope.db")
    campus = CampusService(db, tmp_path / "uploads", provider_factory=lambda: None)
    auth = AuthService(db, tmp_path / "secret")
    teacher = auth.create_user("teacher", "safe-password-123", "teacher")
    service = TeacherService(db, campus)
    course = service.create_course(teacher, "数据库")
    term = service.create_term(teacher, "2026 秋")
    klass = service.create_class(teacher, course["course_id"], term["term_id"], "一班")
    return db, campus, auth, service, teacher, course, klass


def test_import_creates_login_and_forces_password_change(tmp_path, monkeypatch):
    monkeypatch.setenv("ZHIJIAO_STUDENT_DEFAULT_PASSWORD", "initial-password-123")
    db, campus, auth, service, teacher, course, klass = teacher_scope(tmp_path)
    imported = service.import_members(teacher, klass["class_id"], [
        {"student_number": "20260001", "display_name": "张三"},
        {"student_number": "20260001", "display_name": "重复"},
        {"student_number": "bad number"},
    ])
    assert imported["summary"] == {"created": 1, "reused": 0, "already_member": 1, "conflict": 0, "invalid": 1}
    row = db.fetch_one("SELECT * FROM users WHERE student_number='20260001'")
    assert row["password_hash"] != "initial-password-123"
    PasswordHasher().verify(row["password_hash"], "initial-password-123")
    student, _, refresh = auth.login("20260001", "initial-password-123")
    assert student["must_change_password"] == 1
    updated, _, _ = auth.change_password(student, "initial-password-123", "personal-password-456")
    assert updated["must_change_password"] == 0
    with pytest.raises(PermissionDenied):
        auth.refresh(refresh)
    assert campus.list_courses(updated["user_id"], "student")[0]["course_id"] == course["course_id"]


def test_import_reuses_student_and_rejects_teacher_collision(tmp_path, monkeypatch):
    monkeypatch.setenv("ZHIJIAO_STUDENT_DEFAULT_PASSWORD", "initial-password-123")
    _, _, auth, service, teacher, _, klass = teacher_scope(tmp_path)
    existing = auth.create_user("20260002", "existing-password-123", "student")
    auth.create_user("20260003", "teacher-password-123", "teacher")
    result = service.import_members(teacher, klass["class_id"], [
        {"student_number": "20260002"}, {"student_number": "20260003"},
    ])
    assert [row["status"] for row in result["results"]] == ["reused", "conflict"]
    auth.login("20260002", "existing-password-123")
    assert result["members"][0]["user_id"] == existing["user_id"]


def test_csv_and_xlsx_headers_and_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("ZHIJIAO_STUDENT_DEFAULT_PASSWORD", "initial-password-123")
    _, _, _, service, teacher, _, klass = teacher_scope(tmp_path)
    csv_result = service.import_member_file(teacher, klass["class_id"], "students.csv", "学号,姓名\n20260011,李四\n".encode())
    assert csv_result["summary"]["created"] == 1
    workbook = Workbook(); sheet = workbook.active; sheet.append(["student_number", "display_name"]); sheet.append(["20260012", "王五"])
    stream = io.BytesIO(); workbook.save(stream)
    assert service.import_member_file(teacher, klass["class_id"], "students.xlsx", stream.getvalue())["summary"]["created"] == 1
    with pytest.raises(ValidationError, match="缺少"):
        service.import_member_file(teacher, klass["class_id"], "bad.csv", b"name\nfoo\n")
    with pytest.raises(ValidationError, match="5000"):
        service.import_members(teacher, klass["class_id"], [
            {"student_number": f"S{index:05d}"} for index in range(5001)
        ])


class RoutingProvider:
    def generate(self, _system, prompt):
        compact = json.loads(prompt.splitlines()[-1])
        output = []
        for block in compact:
            content = block["content"]
            if "定义" in content:
                destination, role, group = "knowledge", "definition", ""
            elif "答案" in content:
                destination, role, group = "question_bank", "answer", "q1"
            else:
                destination, role, group = "question_bank", "question", "q1"
            output.append({"block_id": block["block_id"], "content_destination": destination,
                           "semantic_role": role, "question_group_key": group,
                           "confidence": .95, "reason": "test"})
        return json.dumps({"blocks": output}, ensure_ascii=False)


def test_ai_routes_examples_separately_without_creating_formal_question_bank(tmp_path):
    db, campus, _, service, teacher, course, _ = teacher_scope(tmp_path)
    campus.provider_factory = RoutingProvider
    ingestion = IngestionService(db, campus)
    body = "# 知识\n关系模型定义\n# 例题\n例题：选择正确关系\n# 答案\n答案：R".encode()
    job = ingestion.queue_document(teacher, course["course_id"], "lesson.md", "text/markdown", body)
    ingestion.process_job(job["job_id"])
    ingestion._classify_document(job["document_id"], course["course_id"])
    blocks = ingestion.list_blocks(teacher, job["document_id"])
    assert {row["content_destination"] for row in blocks} == {"knowledge", "question_bank"}
    questions = ingestion.list_question_bank(teacher, course["course_id"])
    assert questions == []
    knowledge = next(row for row in blocks if row["content_destination"] == "knowledge")
    ingestion.review_block(teacher, knowledge["block_id"], markdown=knowledge["markdown"],
                           plain_text=knowledge["plain_text"], latex="", visibility_level="PUBLIC", accepted=True)
    version = ingestion.publish(teacher, course["course_id"])
    version_blocks = db.fetch_all("SELECT block_id FROM knowledge_version_blocks WHERE version_id=?", (version["version_id"],))
    assert version_blocks == [{"block_id": knowledge["block_id"]}]


def test_ai_failure_keeps_unclassified_and_blocks_publish(tmp_path):
    db, campus, _, _, teacher, course, _ = teacher_scope(tmp_path)
    ingestion = IngestionService(db, campus)
    job = ingestion.queue_document(teacher, course["course_id"], "lesson.txt", "text/plain", b"knowledge")
    ingestion.process_job(job["job_id"])
    assert ingestion.list_blocks(teacher, job["document_id"])[0]["content_destination"] == "unclassified"
    with pytest.raises(ValidationError):
        ingestion.publish(teacher, course["course_id"])
