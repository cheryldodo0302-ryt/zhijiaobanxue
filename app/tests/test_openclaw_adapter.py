import io
import json

import pytest

from auth_service import AuthService
from campus_service import CampusService, ValidationError
from database import LearningDatabase
from integrations.openclaw_adapter import OpenClawAdapter, OpenClawToolError
from integrations.openclaw_adapter.probe import probe
from integrations.openclaw_adapter.server import StdioMcpServer


def _setup(tmp_path):
    db = LearningDatabase(tmp_path / "openclaw.db")
    auth = AuthService(db, tmp_path / "auth-secret")
    actor = auth.create_user("openclaw-student", "strong-password", "student")
    course = CampusService(db).create_course(
        "OpenClaw 课程", "personal_course", actor["user_id"], actor["role"]
    )
    return db, actor, course


def test_adapter_connects_and_preserves_course_scope(tmp_path):
    db, actor, course = _setup(tmp_path)
    adapter = OpenClawAdapter(db, actor)

    status = adapter.invoke({"name": "zhijiao_connection_status"})
    courses = adapter.invoke({"name": "zhijiao_list_courses"})

    assert status["connected"] is True
    assert status["actor"]["user_id"] == actor["user_id"]
    assert [item["course_id"] for item in courses] == [course["course_id"]]
    assert len(adapter.tool_definitions()) == 7


def test_mcp_initialize_list_and_call(tmp_path):
    db, actor, _ = _setup(tmp_path)
    server = StdioMcpServer(OpenClawAdapter(db, actor), io.StringIO(), io.StringIO())

    initialized = server.handle({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18"},
    })
    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    called = server.handle({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "zhijiao_connection_status", "arguments": {}},
    })

    assert initialized["result"]["serverInfo"]["name"] == "zhijiao-database"
    assert any(tool["name"] == "zhijiao_list_courses" for tool in listed["result"]["tools"])
    assert called["result"]["structuredContent"]["connected"] is True
    assert called["result"]["isError"] is False


def test_stdio_process_works_from_another_directory(tmp_path, monkeypatch):
    db, actor, _ = _setup(tmp_path)
    monkeypatch.setenv("ZHIJIAO_OPENCLAW_ACCESS_TOKEN", "stale-inherited-token")
    status = probe(user_id=actor["user_id"], database=str(db.db_path))
    assert status["connected"] is True
    assert status["tool_count"] == 7
    assert status["course_count"] == 1
    assert status["actor"]["user_id"] == actor["user_id"]


def test_invalid_frames_do_not_kill_stdio_or_expose_exceptions(tmp_path):
    db, actor, _ = _setup(tmp_path)
    adapter = OpenClawAdapter(db, actor)
    adapter._handlers["zhijiao_list_courses"] = lambda _: (_ for _ in ()).throw(
        RuntimeError("private-api-key")
    )
    requests = [
        "not-json",
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2099-01-01"}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": []}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "zhijiao_list_courses", "arguments": {}}}),
        json.dumps({"jsonrpc": "2.0", "id": 4, "method": "ping"}),
    ]
    output = io.StringIO()
    StdioMcpServer(adapter, io.StringIO("\n".join(requests)), output).serve()
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert replies[0]["error"]["code"] == -32700
    assert replies[1]["result"]["protocolVersion"] == "2025-06-18"
    assert replies[2]["error"]["code"] == -32602
    assert replies[3]["result"]["isError"] is True
    assert "private-api-key" not in output.getvalue()
    assert replies[4]["result"] == {}


def test_bound_account_is_rechecked_and_other_courses_are_denied(tmp_path):
    db, actor, _ = _setup(tmp_path)
    other = AuthService(db).create_user("another-student", "strong-password", "student")
    course = CampusService(db).create_course("他人私有课", "personal_course", other["user_id"], "student")
    adapter = OpenClawAdapter(db, actor)
    with pytest.raises(OpenClawToolError):
        adapter.invoke({"name": "zhijiao_list_documents", "arguments": {"course_id": course["course_id"]}})
    db.execute("UPDATE users SET status='disabled' WHERE user_id=?", (actor["user_id"],))
    with pytest.raises(OpenClawToolError, match="账号已失效"):
        adapter.invoke({"name": "zhijiao_list_courses"})


@pytest.mark.parametrize("arguments", [[], None, False, "", {"sql": "select * from users"}])
def test_invalid_tool_arguments_are_rejected(tmp_path, arguments):
    db, actor, _ = _setup(tmp_path)
    with pytest.raises(OpenClawToolError):
        OpenClawAdapter(db, actor).invoke({"name": "zhijiao_list_courses", "arguments": arguments})


def test_missing_database_is_not_created(tmp_path):
    target = tmp_path / "missing.db"
    with pytest.raises(ValidationError, match="数据库不存在"):
        OpenClawAdapter.from_environment(user_id="irrelevant", db_path=target)
    assert not target.exists()


def test_course_qa_uses_bound_account_ai_settings(tmp_path, monkeypatch):
    import config
    from account_ai_service import AccountAiService

    db, actor, course = _setup(tmp_path)
    adapter = OpenClawAdapter(db, actor)
    selected = {"mode": "mock", "model": "account-specific"}
    monkeypatch.setattr(AccountAiService, "resolve", lambda self, user_id: selected)

    def ask(*args):
        assert config.get_ai_settings() == selected
        return {"answer": "ok"}

    monkeypatch.setattr(adapter.campus, "ask", ask)
    assert adapter.invoke({"name": "zhijiao_ask_course", "arguments": {
        "course_id": course["course_id"], "question": "问题",
    }}) == {"answer": "ok"}


def test_teacher_analysis_honors_agent_switch(tmp_path, monkeypatch):
    import config

    db = LearningDatabase(tmp_path / "teacher.db")
    teacher = AuthService(db).create_user("teacher", "strong-password", "teacher")
    course = CampusService(db).create_course("教师课", "shared_course", teacher["user_id"], "teacher")
    adapter = OpenClawAdapter(db, teacher)
    request = {"name": "zhijiao_class_analysis", "arguments": {"course_id": course["course_id"]}}
    monkeypatch.setattr(config, "TEACHER_PORTAL_ENABLED", False)
    with pytest.raises(OpenClawToolError, match="当前已禁用"):
        adapter.invoke(request)
    monkeypatch.setattr(config, "TEACHER_PORTAL_ENABLED", True)
    assert adapter.invoke(request)["data_scope"] == "course_aggregate"
