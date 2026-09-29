from agent_service import CampusAgentService
from campus_service import CampusService
import config
from database import LearningDatabase


def test_bundled_demo_teacher_can_use_teacher_service_while_portal_is_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TEACHER_PORTAL_ENABLED", False)
    monkeypatch.setattr(config, "DEMO_TEACHER_ENABLED", True)
    demo_actor = {"user_id": "demo_teacher_001", "role": "teacher"}
    regular_actor = {"user_id": "teacher_001", "role": "teacher"}

    assert config.teacher_portal_enabled_for(demo_actor) is True
    assert config.teacher_portal_enabled_for(regular_actor) is False

    db = LearningDatabase(tmp_path / "demo-access.db")
    campus = CampusService(db, tmp_path / "uploads")
    try:
        service = CampusAgentService(campus)
        demo_result = service.invoke({
            "request_id": "demo-teacher",
            "agent": "teacher_assistant",
            "action": "shared_course_create",
            "actor": demo_actor,
            "input": {"course_name": "演示课程"},
        })
        regular_result = service.invoke({
            "request_id": "regular-teacher",
            "agent": "teacher_assistant",
            "action": "shared_course_create",
            "actor": regular_actor,
            "input": {"course_name": "不应创建"},
        })
        assert demo_result.status == "success"
        assert regular_result.status == "disabled"
    finally:
        db.engine.dispose()


def test_demo_teacher_exception_can_be_disabled(monkeypatch):
    monkeypatch.setattr(config, "TEACHER_PORTAL_ENABLED", False)
    monkeypatch.setattr(config, "DEMO_TEACHER_ENABLED", False)
    assert config.teacher_portal_enabled_for({
        "user_id": "demo_teacher_001",
        "role": "teacher",
    }) is False
