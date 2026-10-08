from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

import config
from auth_service import AuthService
from campus_service import CampusError, CampusService, PermissionDenied, ValidationError
from database import LearningDatabase


class OpenClawMappingNotConfigured(RuntimeError):
    """Compatibility alias retained for callers of the old placeholder."""


class OpenClawToolError(RuntimeError):
    """A safe error that may be returned to an MCP client."""


def _string(arguments: dict[str, Any], name: str, *, max_length: int = 2000) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise OpenClawToolError(f"{name} 不能为空")
    value = value.strip()
    if len(value) > max_length:
        raise OpenClawToolError(f"{name} 不能超过 {max_length} 个字符")
    return value


class OpenClawAdapter:
    """Permission-preserving MCP adapter for the Zhijiao SQLite database.

    OpenClaw never receives raw SQL access. Every operation is dispatched to
    ``CampusService`` with one configured application actor, so the same course
    visibility and role checks used by FastAPI remain in force.
    """

    def __init__(self, db: LearningDatabase, actor: dict[str, Any]):
        if actor.get("status") != "active":
            raise PermissionDenied("OpenClaw 绑定账号不存在或已停用")
        if actor.get("must_change_password"):
            raise PermissionDenied("OpenClaw 绑定账号必须先在网页端修改初始密码")
        if actor.get("role") not in {"student", "teacher"}:
            raise PermissionDenied("OpenClaw 绑定账号角色不受支持")
        self.db = db
        self.actor = actor
        self.campus = CampusService(db)
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "zhijiao_connection_status": self._connection_status,
            "zhijiao_list_courses": self._list_courses,
            "zhijiao_list_documents": self._list_documents,
            "zhijiao_course_knowledge_status": self._knowledge_status,
            "zhijiao_ask_course": self._ask_course,
            "zhijiao_learning_profile": self._learning_profile,
            "zhijiao_class_analysis": self._class_analysis,
        }

    @classmethod
    def from_environment(cls) -> "OpenClawAdapter":
        """Create the stdio adapter from settings supplied by OpenClaw."""
        db_path = Path(os.environ.get("ZHIJIAO_OPENCLAW_DB_PATH", "").strip() or config.DB_PATH)
        db = LearningDatabase(db_path)
        auth = AuthService(db)
        token = os.environ.get("ZHIJIAO_OPENCLAW_ACCESS_TOKEN", "").strip()
        username = os.environ.get("ZHIJIAO_OPENCLAW_USERNAME", "").strip()
        password = os.environ.get("ZHIJIAO_OPENCLAW_PASSWORD", "")
        user_id = os.environ.get("ZHIJIAO_OPENCLAW_USER_ID", "").strip()
        if token:
            actor = auth.authenticate(token)
        elif username and password:
            actor, _, _ = auth.login(username, password, client_id="openclaw-mcp")
        elif user_id:
            actor = auth.get_user(user_id)
        else:
            raise PermissionDenied(
                "未配置 OpenClaw 账号；请设置 ZHIJIAO_OPENCLAW_USER_ID，"
                "或通过 secret 配置 ACCESS_TOKEN/USERNAME/PASSWORD"
            )
        return cls(db, actor)

    @staticmethod
    def tool_definitions() -> list[dict[str, Any]]:
        course_id = {
            "type": "string", "minLength": 1, "maxLength": 80,
            "description": "智教伴学课程 ID；可先调用 zhijiao_list_courses 获取",
        }
        no_args = {"type": "object", "properties": {}, "additionalProperties": False}
        return [
            {
                "name": "zhijiao_connection_status",
                "description": "检查智教伴学数据库连接与当前绑定账号，不返回密码或密钥。",
                "inputSchema": no_args,
                "annotations": {"readOnlyHint": True, "idempotentHint": True},
            },
            {
                "name": "zhijiao_list_courses",
                "description": "列出当前绑定账号有权访问的课程。",
                "inputSchema": no_args,
                "annotations": {"readOnlyHint": True, "idempotentHint": True},
            },
            {
                "name": "zhijiao_list_documents",
                "description": "列出指定课程中当前账号有权查看的资料元数据。",
                "inputSchema": {"type": "object", "properties": {"course_id": course_id},
                                "required": ["course_id"], "additionalProperties": False},
                "annotations": {"readOnlyHint": True, "idempotentHint": True},
            },
            {
                "name": "zhijiao_course_knowledge_status",
                "description": "读取指定课程的资料、分块与知识库就绪状态。",
                "inputSchema": {"type": "object", "properties": {"course_id": course_id},
                                "required": ["course_id"], "additionalProperties": False},
                "annotations": {"readOnlyHint": True, "idempotentHint": True},
            },
            {
                "name": "zhijiao_ask_course",
                "description": "基于指定课程中获准访问的知识内容回答问题，并保存正常的课程问答记录。",
                "inputSchema": {
                    "type": "object", "properties": {
                        "course_id": course_id,
                        "question": {"type": "string", "minLength": 1, "maxLength": 2000},
                    }, "required": ["course_id", "question"], "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": False, "idempotentHint": False},
            },
            {
                "name": "zhijiao_learning_profile",
                "description": "读取当前学生本人在指定课程中的学习画像；教师账号不可调用。",
                "inputSchema": {"type": "object", "properties": {"course_id": course_id},
                                "required": ["course_id"], "additionalProperties": False},
                "annotations": {"readOnlyHint": True, "idempotentHint": True},
            },
            {
                "name": "zhijiao_class_analysis",
                "description": "读取教师本人课程的匿名汇总分析；学生账号不可调用。",
                "inputSchema": {
                    "type": "object", "properties": {
                        "course_id": course_id,
                        "class_id": {"type": "string", "maxLength": 80},
                    }, "required": ["course_id"], "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": True, "idempotentHint": True},
            },
        ]

    def map_project_manifest(self, internal_manifest: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"name": "zhijiao-database", "transport": "stdio",
                "tools": self.tool_definitions(), "metadata": dict(internal_manifest or {})}

    def invoke(self, mapped_request: dict[str, Any]) -> Any:
        name = mapped_request.get("name")
        arguments = mapped_request.get("arguments") or {}
        if not isinstance(name, str) or name not in self._handlers:
            raise OpenClawToolError(f"未知工具：{name!r}")
        if not isinstance(arguments, dict):
            raise OpenClawToolError("工具参数必须是 JSON 对象")
        try:
            return self._handlers[name](arguments)
        except OpenClawToolError:
            raise
        except (CampusError, ValueError) as exc:
            raise OpenClawToolError(str(exc)) from exc

    def _connection_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT 1 ok")
        return {
            "connected": bool(row and row.get("ok") == 1),
            "database": str(self.db.db_path.resolve()),
            "actor": {key: self.actor.get(key, "")
                      for key in ("user_id", "username", "display_name", "role")},
        }

    def _list_courses(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        return self.campus.list_courses(self.actor["user_id"], self.actor["role"])

    def _list_documents(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        return self.campus.list_documents(_string(arguments, "course_id", max_length=80),
                                          self.actor["user_id"], self.actor["role"])

    def _knowledge_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.campus.knowledge_status(_string(arguments, "course_id", max_length=80),
                                            self.actor["user_id"], self.actor["role"])

    def _ask_course(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.campus.ask(_string(arguments, "course_id", max_length=80),
                               self.actor["user_id"], self.actor["role"],
                               _string(arguments, "question", max_length=2000))

    def _learning_profile(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.campus.profile(_string(arguments, "course_id", max_length=80),
                                   self.actor["user_id"], self.actor["role"])

    def _class_analysis(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.actor["role"] != "teacher":
            raise ValidationError("班级分析仅供教师查看")
        class_id = arguments.get("class_id")
        if class_id is not None and not isinstance(class_id, str):
            raise OpenClawToolError("class_id 必须是字符串")
        return self.campus.class_analysis(_string(arguments, "course_id", max_length=80),
                                          self.actor["user_id"],
                                          class_id.strip() if class_id and class_id.strip() else None)
