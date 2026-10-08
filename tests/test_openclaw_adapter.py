import io

from auth_service import AuthService
from campus_service import CampusService
from database import LearningDatabase
from integrations.openclaw_adapter import OpenClawAdapter
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
