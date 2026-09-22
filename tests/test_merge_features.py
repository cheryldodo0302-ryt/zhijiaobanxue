from auth_service import AuthService
from campus_service import CampusService
from database import LearningDatabase
from ingestion_service import IngestionService
from llm_provider import MockProvider, OllamaProvider


def test_teacher_can_save_and_queue_local_ollama_without_api_key(tmp_path):
    db = LearningDatabase(tmp_path / "ollama.db")
    auth = AuthService(db, tmp_path / "auth-secret")
    teacher = auth.create_user("ollama-teacher", "safe-password-123", "teacher")
    campus = CampusService(db, tmp_path / "uploads", provider_factory=MockProvider)
    course = campus.create_course("Ollama 课程", "shared_course", teacher["user_id"], "teacher")
    ingestion = IngestionService(db, campus)

    saved = ingestion.save_teacher_ai_settings(
        teacher,
        provider="ollama",
        base_url="http://127.0.0.1:11434/v1",
        model="qwen2.5:7b",
        api_key="",
    )
    assert saved["provider"] == "ollama"
    assert saved["has_api_key"] is False
    assert isinstance(
        ingestion._custom_ai_provider(
            "ollama", "", saved["base_url"], saved["model"],
        ),
        OllamaProvider,
    )

    job = ingestion.queue_document(
        teacher,
        course["course_id"],
        "lesson.md",
        "text/markdown",
        "# 本地模型\n\nOllama 无需云端 API Key。".encode(),
        ai_settings={"use_saved": True},
    )
    stored = db.fetch_one("SELECT * FROM ingestion_jobs WHERE job_id=?", (job["job_id"],))
    assert stored["ai_provider"] == "ollama"
    assert stored["ai_base_url"] == "http://127.0.0.1:11434/v1"
    assert stored["ai_key_encrypted"] == ""
