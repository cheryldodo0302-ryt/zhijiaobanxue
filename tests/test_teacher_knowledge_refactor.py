import io
from pathlib import Path

from pptx import Presentation

from auth_service import AuthService
from campus_service import CampusService
from database import LearningDatabase
from formula_client import Pix2TextClient
from ingestion_service import IngestionService
from mineru_client import MinerUClient
from teacher_service import TeacherService


def teacher_scope(tmp_path: Path):
    db = LearningDatabase(tmp_path / "knowledge-refactor.db")
    campus = CampusService(db, tmp_path / "uploads", provider_factory=lambda: None)
    teacher = AuthService(db, tmp_path / "secret").create_user(
        "teacher", "safe-password-123", "teacher"
    )
    course = TeacherService(db, campus).create_course(teacher, "数据库")
    return db, campus, teacher, course


class ForbiddenMinerU:
    enabled = True

    def parse(self, *_args, **_kwargs):
        raise AssertionError("native text/Office content must not invoke MinerU")


class DisabledFormula:
    enabled = False


class RecordingMinerU:
    enabled = True

    def __init__(self):
        self.method = ""

    def parse(self, _path, *, method, asset_dir):
        self.method = method
        return {
            "_version_name": "test",
            "_markdown": "# 第一章\n\n关系模型定义",
            "_image_paths": {},
            "pdf_info": [{
                "page_idx": 0,
                "para_blocks": [
                    {"type": "title", "content": "# 第一章"},
                    {"type": "text", "content": "关系模型定义"},
                ],
            }],
        }


def test_txt_uses_native_markdown_and_inline_text_preview(tmp_path):
    db, campus, teacher, course = teacher_scope(tmp_path)
    service = IngestionService(db, campus)
    service.mineru = ForbiddenMinerU()
    body = "第一节\n关系模型定义"
    job = service.queue_document(
        teacher, course["course_id"], "lesson.txt", "text/plain", body.encode()
    )
    service.process_job(job["job_id"])
    artifact = db.fetch_one(
        "SELECT * FROM document_artifacts WHERE document_id=? AND artifact_type='canonical_markdown'",
        (job["document_id"],),
    )
    assert artifact and artifact["status"] == "ready"
    assert Path(artifact["stored_path"]).read_text(encoding="utf-8").strip() == body
    assert service.preview_descriptor(teacher, job["document_id"])["preview_kind"] == "text"
    media_type, preview = service.preview_file(teacher, job["document_id"])
    assert media_type == "text/plain" and preview == body


def test_pptx_uses_native_text_and_supports_browser_preview_without_converter(tmp_path, monkeypatch):
    db, campus, teacher, course = teacher_scope(tmp_path)
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "关系模型"
    slide.placeholders[1].text = "实体与关系"
    stream = io.BytesIO()
    presentation.save(stream)
    service = IngestionService(db, campus)
    service.mineru = ForbiddenMinerU()
    monkeypatch.setattr("ingestion_service.shutil.which", lambda _name: None)
    job = service.queue_document(
        teacher, course["course_id"], "lesson.pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation", stream.getvalue(),
    )
    service.process_job(job["job_id"])
    descriptor = service.preview_descriptor(teacher, job["document_id"])
    assert descriptor["preview_kind"] == "pptx"
    assert descriptor["conversion_status"] == "ready"
    assert db.fetch_one(
        "SELECT 1 ok FROM document_artifacts WHERE document_id=? AND artifact_type='canonical_markdown' AND status='ready'",
        (job["document_id"],),
    )


def test_teacher_pdf_uses_mineru_auto_and_persists_returned_markdown(tmp_path):
    db, campus, teacher, course = teacher_scope(tmp_path)
    service = IngestionService(db, campus)
    mineru = RecordingMinerU()
    service.mineru = mineru
    service.formula = DisabledFormula()
    job = service.queue_document(
        teacher, course["course_id"], "lesson.pdf", "application/pdf", b"pdf fixture"
    )
    service.process_job(job["job_id"])
    assert mineru.method == "auto"
    artifact = db.fetch_one(
        "SELECT * FROM document_artifacts WHERE document_id=? AND artifact_type='canonical_markdown'",
        (job["document_id"],),
    )
    assert artifact and "关系模型定义" in Path(artifact["stored_path"]).read_text(encoding="utf-8")
    blocks = db.fetch_all("SELECT * FROM document_blocks WHERE document_id=?", (job["document_id"],))
    assert {row["parser_name"] for row in blocks} == {"MinerU"}


def test_remote_clients_attach_bearer_tokens_and_verify_tls(monkeypatch):
    monkeypatch.setenv("ZHIJIAO_MINERU_TOKEN", "mineru-secret")
    monkeypatch.setenv("ZHIJIAO_FORMULA_TOKEN", "formula-secret")
    monkeypatch.setenv("ZHIJIAO_MINERU_VERIFY_TLS", "1")
    monkeypatch.setenv("ZHIJIAO_FORMULA_VERIFY_TLS", "1")
    mineru = MinerUClient("https://mineru.example.edu")
    formula = Pix2TextClient("https://formula.example.edu")
    assert mineru.headers == {"Authorization": "Bearer mineru-secret"}
    assert formula.headers == {"Authorization": "Bearer formula-secret"}
    assert mineru.verify_tls is True and formula.verify_tls is True
