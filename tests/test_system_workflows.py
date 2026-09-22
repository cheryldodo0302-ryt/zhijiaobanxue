from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

import config
import job_secret_store
from account_ai_service import AccountAiService
from agent_service import CampusAgentService
from auth_service import AuthService
from campus_service import CampusService, PermissionDenied, ValidationError
from database import LearningDatabase
from ingestion_service import IngestionService
from llm_provider import MockProvider
from skills.memory import MemoryLearningSkill
from teacher_service import TeacherService


@pytest.fixture()
def system(tmp_path, monkeypatch):
    monkeypatch.setattr(job_secret_store, "KEY_PATH", tmp_path / "key")
    monkeypatch.setenv("ZHIJIAO_AI_MODE", "mock")
    monkeypatch.setenv("ZHIJIAO_STUDENT_DEFAULT_PASSWORD", "initial-password-123")
    db = LearningDatabase(tmp_path / "audit.db")
    campus = CampusService(db, tmp_path / "uploads", provider_factory=MockProvider)
    auth = AuthService(db, tmp_path / "auth-secret")
    teacher = auth.create_user("teacher", "safe-password-123", "teacher")
    student = auth.create_user("student", "safe-password-123", "student")
    return db, campus, auth, teacher, student


def test_account_ai_settings_isolate_keys_endpoints_and_concurrent_calls(system):
    db, campus, auth, teacher, student = system
    other = auth.create_user("other", "safe-password-123", "student")
    settings = AccountAiService(db)
    public = settings.save(student, mode="custom", provider="openai_compatible",
                           base_url="https://example.com/v1", model="student-model", api_key="private-test-key")
    assert public["scope"] == "account" and public["has_api_key"]
    assert "private-test-key" not in str(public)
    assert "private-test-key" not in str(db.fetch_all("SELECT * FROM account_ai_settings"))
    assert settings.public(other)["mode"] == "mock"
    assert settings.public(teacher)["mode"] == "mock"
    assert config.get_ai_settings()["mode"] == "mock"
    settings.save(student, mode="custom", provider="openai_compatible",
                  base_url="https://example.com/v1", model="next-model")
    with pytest.raises(ValidationError, match="API Key"):
        settings.save(student, mode="custom", provider="openai_compatible",
                      base_url="https://example.org/v1", model="next-model")
    assert settings.resolve(student["user_id"])["api_key"] == "private-test-key"
    with pytest.raises(PermissionDenied):
        settings.save({**student, "role": "teacher"}, mode="mock")

    agent = CampusAgentService(campus)
    agent._dispatch = lambda *_: config.get_ai_settings()["model"]
    def invoke(actor):
        return agent.invoke({"request_id":"isolation", "agent":"student_assistant",
                             "action":"available_courses_list", "actor":actor}).data
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, [student, other] * 4))
    assert results == ["next-model", "mock-course-assistant"] * 4
    assert config.get_ai_settings()["mode"] == "mock"


def test_teacher_saved_key_cannot_follow_a_changed_endpoint(system):
    db, campus, _, teacher, _ = system
    service = IngestionService(db, campus)
    service.save_teacher_ai_settings(teacher, provider="openai_compatible",
                                    base_url="https://example.com/v1", model="model", api_key="test-secret")
    with pytest.raises(ValidationError, match="API Key"):
        service.save_teacher_ai_settings(teacher, provider="openai_compatible",
                                        base_url="https://example.org/v1", model="model")
    with pytest.raises(ValidationError, match="API Key"):
        service._resolve_ai_settings(teacher, {"use_saved":True, "base_url":"https://example.org/v1"})


def test_shared_source_status_and_cards_use_published_knowledge_only(system):
    db, campus, _, teacher, student = system
    course = campus.create_course("共享课", "shared_course", teacher["user_id"], "teacher")
    course_id, student_id = course["course_id"], student["user_id"]
    campus.enroll_student(course_id, teacher["user_id"], student_id)
    ingestion = IngestionService(db, campus)
    job = ingestion.queue_document(teacher, course_id, "source.txt", "text/plain",
                                   "关系模型用二维表表达数据关系。".encode(), analysis_mode="local")
    ingestion.process_job(job["job_id"])
    analysis = db.fetch_one("SELECT analysis_job_id FROM semantic_analysis_jobs WHERE document_id=?", (job["document_id"],))
    if analysis:
        ingestion.process_semantic_analysis(analysis["analysis_job_id"])
    memory = MemoryLearningSkill(campus)
    assert campus.list_documents(course_id, student_id, "student") == []
    assert campus._retriever(course_id).search("关系模型") == []
    legacy = campus.upload_document(course_id, teacher["user_id"], "teacher", "legacy.txt", "text/plain",
                                    "这是尚未发布的独有词红杉算法。".encode())
    assert campus._retriever(course_id).search("红杉算法") == []
    campus.delete_document(legacy["document_id"], teacher["user_id"], "teacher")
    with pytest.raises(ValidationError, match="已发布"):
        memory.build_blocks(course_id, student_id, job["document_id"])
    ingestion.approve_document_knowledge(teacher, job["document_id"])
    ingestion.publish(teacher, course_id)
    db.execute("UPDATE course_documents SET student_file_visible=1 WHERE document_id=?", (job["document_id"],))
    docs = campus.list_documents(course_id, student_id, "student")
    assert len(docs) == 1 and "text_preview" not in docs[0]
    assert ingestion.list_student_source_files(student, course_id) == docs
    agent = CampusAgentService(campus)
    response = agent.invoke({"request_id":"docs", "agent":"student_assistant", "action":"document_status",
                             "actor":student, "scope":{"course_id":course_id}})
    assert response.data == docs
    campus.provider_factory = Mock(side_effect=AssertionError("shared cards must not regenerate teacher content"))
    cards = memory.build_blocks(course_id, student_id)
    assert cards
    assert len(memory.build_blocks(course_id, student_id)) == len(cards)
    db.execute("UPDATE course_documents SET student_file_visible=0 WHERE document_id=?", (job["document_id"],))
    assert campus.list_documents(course_id, student_id, "student") == []


def test_personal_card_preparation_reuses_existing_and_keeps_document_sources(system, monkeypatch):
    _, campus, _, _, student = system
    course = campus.create_course("个人课", "personal_course", student["user_id"], "student")
    doc_ids = []
    for number in (1, 2):
        doc = campus.upload_document(course["course_id"], student["user_id"], "student", f"{number}.txt",
                                     "text/plain", f"第{number}份课程原文。".encode())
        doc_ids.append(doc["document_id"])
    semantic = Mock(side_effect=lambda rows: [{"title":rows[0]["original_name"], "keywords":["原文"], "content":rows[0]["content"]}])
    monkeypatch.setattr("skills.memory.service.SemanticKnowledgeService.semantic_chunks", semantic)
    memory = MemoryLearningSkill(campus)
    cards = memory.build_blocks(course["course_id"], student["user_id"])
    again = memory.build_blocks(course["course_id"], student["user_id"])
    assert len(cards) == len(again) == 2
    assert semantic.call_count == 2
    assert {row["document_id"] for row in memory.list_blocks(course["course_id"], student["user_id"])} == set(doc_ids)


def test_class_move_and_delete_synchronize_student_course_access(system):
    _, campus, _, teacher, _ = system
    service = TeacherService(campus.db, campus)
    first = service.create_course(teacher, "原课程")["course_id"]
    second = service.create_course(teacher, "新课程")["course_id"]
    term = service.create_term(teacher, "2026秋")["term_id"]
    first_class = service.create_class(teacher, first, term, "一班")["class_id"]
    other_class = service.create_class(teacher, first, term, "二班")["class_id"]
    member = service.add_members(teacher, first_class, ["20260001"])["members"][0]["user_id"]
    service.add_members(teacher, other_class, ["20260001"])
    service.update_class(teacher, first_class, {"course_id":second})
    assert campus.require_access(second, member, "student")
    assert campus.require_access(first, member, "student")  # The other class still grants access.
    service.delete_class(teacher, other_class)
    with pytest.raises(PermissionDenied):
        campus.require_access(first, member, "student")
    service.delete_class(teacher, first_class)
    with pytest.raises(PermissionDenied):
        campus.require_access(second, member, "student")
