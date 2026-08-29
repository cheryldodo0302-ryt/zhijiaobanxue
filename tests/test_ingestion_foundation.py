from pathlib import Path
from io import BytesIO

import pytest

from auth_service import AuthService
from campus_service import CampusService, PermissionDenied, ValidationError
from database import LearningDatabase
from document_ir import formula_anomalies, mineru_to_blocks, normalize_latex, search_aliases
from ingestion_service import IngestionService
from job_secret_store import decrypt_job_secret
from semantic_knowledge_service import SemanticKnowledgeService
from teacher_service import TeacherService


@pytest.fixture()
def setup(tmp_path: Path):
    db = LearningDatabase(tmp_path / "ingestion.db")
    campus = CampusService(db, tmp_path / "uploads", provider_factory=lambda: None)
    teacher = AuthService(db, tmp_path / "secret").create_user("teacher", "safe-password-123", "teacher")
    course = TeacherService(db, campus).create_course(teacher, "可信知识库")
    return db, campus, IngestionService(db, campus), teacher, course


def test_native_job_review_publish_and_public_retrieval(setup):
    db, campus, ingestion, teacher, course = setup
    job = ingestion.queue_document(
        teacher, course["course_id"], "lesson.md", "text/markdown", "选择 σ，投影 π，连接 ⋈。".encode("utf-8")
    )
    assert job["status"] == "queued"
    ingestion.process_job(job["job_id"])
    complete = ingestion.get_job(teacher, job["job_id"])
    assert complete["status"] == "review_required"
    blocks = ingestion.list_blocks(teacher, complete["document_id"])
    assert blocks and "σ" in blocks[0]["plain_text"]
    with pytest.raises(ValidationError):
        ingestion.publish(teacher, course["course_id"])
    for block in blocks:
        ingestion.review_block(
            teacher, block["block_id"], markdown=block["markdown"], plain_text=block["plain_text"],
            latex=block["latex"], visibility_level="PUBLIC", accepted=True,
        )
    version = ingestion.publish(teacher, course["course_id"])
    assert version["status"] == "published"
    rows = campus._retriever(course["course_id"]).rows
    assert rows and "σ" in rows[0]["content"]


def test_vault_is_never_in_published_student_retrieval(setup):
    _, campus, ingestion, teacher, course = setup
    job = ingestion.queue_document(teacher, course["course_id"], "secret.txt", "text/plain", "期末答案".encode())
    ingestion.process_job(job["job_id"])
    block = ingestion.list_blocks(teacher, job["document_id"])[0]
    ingestion.review_block(
        teacher, block["block_id"], markdown=block["markdown"], plain_text=block["plain_text"], latex="",
        visibility_level="VAULT", accepted=True,
    )
    ingestion.publish(teacher, course["course_id"])
    assert campus._retriever(course["course_id"]).rows == []


def test_document_ir_preserves_formula_bbox_and_database_aliases():
    payload = {
        "_version_name": "test",
        "pdf_info": [{"page_idx": 2, "para_blocks": [{
            "type": "interline_equation", "bbox": [10, 20, 30, 40],
            "lines": [{"spans": [{"content": r"\sigma_{A}(R) ⋈ S"}]}],
        }]}],
    }
    block = mineru_to_blocks(payload)[0]
    assert block["block_type"] == "formula"
    assert block["page_number"] == 3
    assert block["bbox"] == [10, 20, 30, 40]
    assert "连接" in block["search_aliases"]
    assert search_aliases("σ π →") == ["函数依赖", "投影", "选择"]
    assert formula_anomalies(r"\frac{a}{b") == ["latex_braces_unbalanced"]
    assert normalize_latex(r"\mathsf { E } = \mathrm { m } c ^ { 2 }") == "E=mc^{2}"
    assert normalize_latex(r"R ( A , \ B )") == "R(A,B)"


def test_cancel_retry_and_health_report(setup):
    _, _, ingestion, teacher, course = setup
    job = ingestion.queue_document(teacher, course["course_id"], "lesson.txt", "text/plain", b"normal text")
    assert ingestion.cancel_job(teacher, job["job_id"])["status"] == "cancelled"
    assert ingestion.retry_job(teacher, job["job_id"])["status"] == "queued"
    ingestion.process_job(job["job_id"])
    health = ingestion.course_health(teacher, course["course_id"])
    assert health["total_pages"] == 1
    assert health["native_pages"] == 1
    assert health["cloud_model_calls"] == health["cloud_tokens"] == 0


def test_empty_review_document_can_be_reparsed(setup):
    db, _, ingestion, teacher, course = setup
    job = ingestion.queue_document(
        teacher, course["course_id"], "scanned.pdf", "application/pdf", b"%PDF-1.4\npdf fixture"
    )
    db.execute(
        "UPDATE ingestion_jobs SET status='review_required' WHERE job_id=?", (job["job_id"],)
    )
    db.execute(
        "UPDATE course_documents SET status='review_required' WHERE document_id=?",
        (job["document_id"],),
    )
    retried = ingestion.retry_job(teacher, job["job_id"])
    assert retried["status"] == "queued"


def test_teacher_can_choose_local_analysis_without_api_calls(setup):
    db, _, ingestion, teacher, course = setup
    job = ingestion.queue_document(
        teacher, course["course_id"], "local.md", "text/markdown",
        "# 第一章 数据模型\n\n关系模型由关系、属性和元组构成。".encode("utf-8"),
        analysis_mode="local",
    )
    assert job["analysis_mode"] == "local"
    ingestion.process_job(job["job_id"])
    semantic = db.fetch_one(
        "SELECT analysis_job_id FROM semantic_analysis_jobs WHERE document_id=?",
        (job["document_id"],),
    )
    ingestion.process_semantic_analysis(semantic["analysis_job_id"])
    result = ingestion.get_analysis_job(teacher, semantic["analysis_job_id"])
    assert result["status"] == "review_required"
    assert result["analysis_mode"] == "local"
    assert result["api_calls"] == 0
    assert result["progress"] == 100
    assert ingestion.document_outline(teacher, job["document_id"])["nodes"]


def test_changing_analysis_mode_replaces_active_provider_job(setup):
    db, _, ingestion, teacher, course = setup
    upload = ingestion.queue_document(
        teacher, course["course_id"], "switch.md", "text/markdown",
        "# 第一章\n\n关系模型由关系、属性和元组构成。".encode("utf-8"),
        analysis_mode="api",
    )
    ingestion.process_job(upload["job_id"])
    old_job = db.fetch_one(
        """SELECT * FROM semantic_analysis_jobs
           WHERE document_id=? AND status='queued'""",
        (upload["document_id"],),
    )
    replacement = ingestion.queue_semantic_analysis(
        teacher, upload["document_id"], analysis_mode="local"
    )
    old_job = db.fetch_one(
        "SELECT * FROM semantic_analysis_jobs WHERE analysis_job_id=?",
        (old_job["analysis_job_id"],),
    )
    assert old_job["status"] == "cancelled"
    assert replacement["analysis_job_id"] != old_job["analysis_job_id"]
    assert replacement["analysis_mode"] == "local"


def test_quota_or_key_failure_is_not_retried_three_times():
    class ExhaustedProvider:
        calls = 0

        def generate_json(self, _system, _prompt):
            self.calls += 1
            raise RuntimeError("智能服务 API 调用失败（HTTP 403）：Free quota exhausted")

    provider = ExhaustedProvider()
    semantic = SemanticKnowledgeService(lambda: provider)
    with pytest.raises(ValidationError, match="不会重复消耗三次请求"):
        semantic._generate_json("system", "prompt")
    assert provider.calls == 1


def test_teacher_custom_analysis_key_is_encrypted_for_worker(setup, tmp_path, monkeypatch):
    db, _, ingestion, teacher, course = setup
    monkeypatch.setattr("job_secret_store.KEY_PATH", tmp_path / "job-secret.key")
    job = ingestion.queue_document(
        teacher, course["course_id"], "custom.md", "text/markdown",
        "# 第一章\n\n知识正文".encode("utf-8"), analysis_mode="api",
        ai_settings={
            "provider": "openai_compatible",
            "base_url": "https://example.com/v1",
            "model": "teacher-model",
            "api_key": "teacher-secret",
        },
    )
    stored = db.fetch_one(
        "SELECT ai_key_encrypted FROM ingestion_jobs WHERE job_id=?", (job["job_id"],)
    )
    assert stored["ai_key_encrypted"] != "teacher-secret"
    assert decrypt_job_secret(stored["ai_key_encrypted"]) == "teacher-secret"
    assert "ai_key_encrypted" not in job


def test_streaming_upload_rejects_oversize_without_leaving_partial_file(setup, monkeypatch):
    _, campus, ingestion, teacher, course = setup
    monkeypatch.setattr("ingestion_service.MAX_UPLOAD_BYTES", 5)
    with pytest.raises(ValidationError, match="文件不能超过"):
        ingestion.queue_document_stream(
            teacher, course["course_id"], "large.txt", "text/plain", BytesIO(b"123456")
        )
    assert list((campus.storage_dir / course["course_id"]).glob("*")) == []


def test_original_file_and_published_markdown_have_separate_student_permissions(setup, tmp_path: Path):
    db, campus, ingestion, teacher, course = setup
    auth = AuthService(db, tmp_path / "secret-2")
    student = auth.create_user("student", "safe-password-456", "student")
    campus.enroll_student(course["course_id"], teacher["user_id"], student["user_id"])
    job = ingestion.queue_document_stream(
        teacher, course["course_id"], "source.txt", "text/plain", BytesIO("知识库 Markdown".encode())
    )
    ingestion.process_job(job["job_id"])
    block = ingestion.list_blocks(teacher, job["document_id"])[0]
    ingestion.review_block(
        teacher, block["block_id"], markdown=block["markdown"], plain_text=block["plain_text"],
        latex="", visibility_level="PUBLIC", accepted=True,
    )
    ingestion.publish(teacher, course["course_id"])
    with pytest.raises(PermissionDenied):
        ingestion.source_file(student, job["document_id"])
    ingestion.set_student_file_visibility(teacher, job["document_id"], True)
    listed = ingestion.list_student_source_files(student, course["course_id"])
    assert listed[0]["original_name"] == "source.txt"
    _, source = ingestion.source_file(student, job["document_id"])
    assert source.read_text(encoding="utf-8") == "知识库 Markdown"
    token = auth.issue_document_token(student, job["document_id"])
    assert auth.authenticate_document_token(token, job["document_id"])["user_id"] == student["user_id"]
