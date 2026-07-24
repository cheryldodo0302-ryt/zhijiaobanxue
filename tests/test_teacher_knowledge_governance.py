from pathlib import Path

import pytest

from auth_service import AuthService
from campus_service import CampusService, PermissionDenied, ValidationError
from database import LearningDatabase
from ingestion_service import IngestionService
from semantic_knowledge_service import SemanticKnowledgeService
from teacher_service import TeacherService


@pytest.fixture()
def governance(tmp_path: Path):
    db = LearningDatabase(tmp_path / "governance.db")
    campus = CampusService(db, tmp_path / "uploads", provider_factory=lambda: None)
    auth = AuthService(db, tmp_path / "secret")
    teacher = auth.create_user("teacher", "safe-password-123", "teacher")
    other = auth.create_user("other", "safe-password-456", "teacher")
    course = TeacherService(db, campus).create_course(teacher, "通用课程")
    return db, campus, IngestionService(db, campus), teacher, other, course


def test_material_routing_is_content_based_and_teacher_confirmable(governance):
    _, _, service, teacher, other, course = governance
    job = service.queue_document(
        teacher,
        course["course_id"],
        "upload.txt",
        "text/plain",
        "课程目标\n学时分配\n考核方式\n课程内容".encode(),
    )
    service.process_job(job["job_id"])
    row = service.list_jobs(teacher, course["course_id"])[0]
    assert row["suggested_material_type"] == "syllabus"
    assert row["classification_status"] == "suggested"

    confirmed = service.update_material_metadata(
        teacher, job["document_id"], material_type="lesson_plan", tags=["第一章", "重点", "第一章"]
    )
    assert confirmed["material_type"] == "lesson_plan"
    assert confirmed["classification_status"] == "confirmed"
    assert confirmed["tags"] == ["第一章", "重点"]
    with pytest.raises(PermissionDenied):
        service.update_material_metadata(
            other, job["document_id"], material_type="slides", tags=[]
        )


def test_unpublished_erroneous_document_deletes_derived_data_and_files(governance):
    db, _, service, teacher, _, course = governance
    job = service.queue_document(
        teacher, course["course_id"], "wrong.txt", "text/plain", "错误资料".encode()
    )
    service.process_job(job["job_id"])
    stored_path = Path(db.fetch_one(
        "SELECT stored_path FROM course_documents WHERE document_id=?", (job["document_id"],)
    )["stored_path"])
    result = service.delete_document(teacher, job["document_id"])
    assert result["deleted"] is True
    assert not stored_path.exists()
    assert db.fetch_one(
        "SELECT 1 ok FROM course_documents WHERE document_id=?", (job["document_id"],)
    ) is None
    assert db.fetch_one(
        "SELECT 1 ok FROM document_blocks WHERE document_id=?", (job["document_id"],)
    ) is None


def test_documents_support_batch_delete_with_per_item_result(governance):
    db, _, service, teacher, _, course = governance
    jobs = [
        service.queue_document(
            teacher, course["course_id"], f"wrong-{index}.txt", "text/plain",
            f"错误资料 {index}".encode(),
        )
        for index in range(2)
    ]
    for job in jobs:
        service.process_job(job["job_id"])
    result = service.delete_documents(
        teacher, [job["document_id"] for job in jobs]
    )
    assert set(result["deleted"]) == {job["document_id"] for job in jobs}
    assert result["failed"] == []
    assert db.fetch_one(
        "SELECT 1 ok FROM course_documents WHERE course_id=? LIMIT 1",
        (course["course_id"],),
    ) is None


def test_published_document_is_protected_from_hard_delete(governance):
    db, _, service, teacher, _, course = governance
    job = service.queue_document(
        teacher, course["course_id"], "published.txt", "text/plain", "正式资料".encode()
    )
    service.process_job(job["job_id"])
    block = db.fetch_one(
        "SELECT block_id FROM document_blocks WHERE document_id=? LIMIT 1", (job["document_id"],)
    )
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO knowledge_versions(
                   version_id,course_id,version_number,status,created_by,published_at
               ) VALUES('version_1',?,1,'published',?,CURRENT_TIMESTAMP)""",
            (course["course_id"], teacher["user_id"]),
        )
        conn.execute(
            "INSERT INTO knowledge_version_blocks(version_id,block_id) VALUES('version_1',?)",
            (block["block_id"],),
        )
    with pytest.raises(ValidationError, match="发布版本"):
        service.delete_document(teacher, job["document_id"])


def test_rejected_subtree_is_isolated_restorable_and_permanently_deletable(governance):
    db, _, service, teacher, _, course = governance
    chapter_id, section_id, point_id = "kn_chapter", "kn_section", "kn_point"
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO knowledge_nodes(
                   node_id,course_id,node_scope,node_type,title,sort_order,status
               ) VALUES(?,?,'course','chapter','第一章',1,'draft')""",
            (chapter_id, course["course_id"]),
        )
        conn.execute(
            """INSERT INTO knowledge_nodes(
                   node_id,course_id,node_scope,parent_id,node_type,title,sort_order,status
               ) VALUES(?,?,'course',?,'section','第一节',1,'draft')""",
            (section_id, course["course_id"], chapter_id),
        )
        conn.execute(
            """INSERT INTO knowledge_nodes(
                   node_id,course_id,node_scope,parent_id,node_type,title,markdown,sort_order,status
               ) VALUES(?,?,'course',?,'knowledge_point','知识点','原文',1,'draft')""",
            (point_id, course["course_id"], section_id),
        )

    service.update_node(
        teacher, chapter_id, {"status": "rejected", "reason": "超出课程范围"}
    )
    assert service.course_outline(teacher, course["course_id"])["nodes"] == []
    trash = service.list_trash(teacher, course["course_id"])
    assert {row["node_id"] for row in trash} == {chapter_id, section_id, point_id}
    assert {row["reason"] for row in trash} == {"超出课程范围"}

    restored = service.restore_trash_node(teacher, chapter_id)
    assert set(restored["restored_node_ids"]) == {chapter_id, section_id, point_id}
    assert service.list_trash(teacher, course["course_id"]) == []
    assert {row["status"] for row in service.course_outline(
        teacher, course["course_id"]
    )["nodes"]} == {"draft"}

    service.update_node(teacher, point_id, {"status": "rejected"})
    deleted = service.permanently_delete_trash_node(teacher, point_id)
    assert deleted["deleted"] is True
    assert db.fetch_one("SELECT 1 ok FROM knowledge_nodes WHERE node_id=?", (point_id,)) is None


def test_semantic_timeout_enters_checkpointed_retry_wait(governance, monkeypatch):
    db, _, service, teacher, _, course = governance
    job = service.queue_document(
        teacher, course["course_id"], "lesson.txt", "text/plain", "课程知识".encode()
    )
    service.process_job(job["job_id"])
    analysis = db.fetch_one(
        "SELECT * FROM semantic_analysis_jobs WHERE document_id=?",
        (job["document_id"],),
    )
    monkeypatch.setattr(service.semantic, "preflight", lambda: None)

    def timeout(_job, _blocks):
        raise ValidationError("智能服务暂时超时，已保存分析进度并等待自动续跑")

    monkeypatch.setattr(service, "_process_evidence_tree_analysis", timeout)
    service.process_semantic_analysis(analysis["analysis_job_id"])
    state = db.fetch_one(
        "SELECT * FROM semantic_analysis_jobs WHERE analysis_job_id=?",
        (analysis["analysis_job_id"],),
    )
    assert state["status"] == "retry_wait"
    assert state["current_stage"] == "waiting_for_service"
    assert state["retry_count"] == 1
    assert state["next_retry_at"]


def test_transient_ai_timeout_is_not_repeated_three_times_inside_one_call():
    class TimeoutProvider:
        calls = 0

        def generate_json(self, _system, _prompt):
            self.calls += 1
            raise RuntimeError("ReadTimeout: read timed out")

    provider = TimeoutProvider()
    semantic = SemanticKnowledgeService(lambda: provider)
    with pytest.raises(ValidationError, match="自动续跑"):
        semantic._generate_json("system", "prompt")
    assert provider.calls == 1


def test_invalid_json_is_not_repeated_three_times_before_safe_fallback():
    class InvalidJsonProvider:
        calls = 0

        def generate_json(self, _system, _prompt):
            self.calls += 1
            raise RuntimeError('智能服务未返回有效 JSON：{"classifications": [')

    provider = InvalidJsonProvider()
    semantic = SemanticKnowledgeService(lambda: provider)
    with pytest.raises(ValidationError, match="安全降级"):
        semantic._generate_json("system", "prompt")
    assert provider.calls == 1


def test_truncated_json_uses_original_text_fallback_instead_of_failing(
    governance, monkeypatch
):
    db, _, service, teacher, _, course = governance
    job = service.queue_document(
        teacher,
        course["course_id"],
        "lesson.txt",
        "text/plain",
        "第一章 数据模型\n关系模型由关系组成。\n主键用于唯一标识元组。".encode(),
    )
    service.process_job(job["job_id"])
    analysis = db.fetch_one(
        "SELECT * FROM semantic_analysis_jobs WHERE document_id=?",
        (job["document_id"],),
    )
    monkeypatch.setattr(service.semantic, "preflight", lambda: None)
    monkeypatch.setattr(
        service.semantic,
        "analyze_document_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValidationError('智能服务未返回有效 JSON：{"classifications": [')
        ),
    )
    monkeypatch.setattr(
        service.semantic,
        "reduce_document_outline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValidationError("模型响应格式不符合要求")
        ),
    )
    monkeypatch.setattr(
        service.semantic,
        "unify_course_outline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValidationError("课程目录必须返回 JSON 对象")
        ),
    )
    service.process_semantic_analysis(analysis["analysis_job_id"])
    completed = service.get_analysis_job(teacher, analysis["analysis_job_id"])
    assert completed["status"] == "review_required"
    assert completed["analysis_summary"]["fallback_batches"] >= 1
    assert completed["analysis_summary"]["used_document_fallback"] is True
    assert completed["analysis_summary"]["used_course_fallback"] is True
    nodes = service.document_outline(teacher, job["document_id"])["nodes"]
    assert any(node["node_type"] == "knowledge_point" for node in nodes)
    assert any("关系模型" in node["markdown"] for node in nodes)


def test_image_only_material_is_skipped_without_ai_call(governance, monkeypatch):
    db, _, service, teacher, _, course = governance
    job = service.queue_document(
        teacher, course["course_id"], "slides.txt", "text/plain", "占位".encode()
    )
    service.process_job(job["job_id"])
    db.execute(
        """UPDATE document_blocks SET block_type='image',markdown='',plain_text='',latex=''
           WHERE document_id=?""",
        (job["document_id"],),
    )
    analysis = db.fetch_one(
        "SELECT * FROM semantic_analysis_jobs WHERE document_id=?",
        (job["document_id"],),
    )
    monkeypatch.setattr(service.semantic, "preflight", lambda: None)
    called = {"value": False}

    def forbidden(*_args, **_kwargs):
        called["value"] = True
        raise AssertionError("image-only material must not call the model")

    monkeypatch.setattr(service.semantic, "analyze_document_batch", forbidden)
    service.process_semantic_analysis(analysis["analysis_job_id"])
    completed = service.get_analysis_job(teacher, analysis["analysis_job_id"])
    assert completed["status"] == "review_required"
    assert completed["analysis_summary"]["skipped_blocks"] >= 1
    assert called["value"] is False
    block = db.fetch_one(
        "SELECT * FROM document_blocks WHERE document_id=?", (job["document_id"],)
    )
    assert block["content_destination"] == "excluded"
    assert block["semantic_role"] == "image_skipped"
