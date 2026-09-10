from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

from campus_service import CampusError, CampusService, ValidationError
from config import TEACHER_PORTAL_ENABLED
from skills.memory import MemoryLearningSkill


STUDENT_ACTIONS = {
    "personal_course_create", "personal_course_delete", "available_courses_list", "course_select",
    "student_document_upload", "student_document_delete", "document_status",
    "course_qa", "quiz_generate", "quiz_submit", "learning_profile",
    "wrong_question_list", "weak_point_analysis", "personal_data_export",
    "image_text_extract", "knowledge_blocks_build", "knowledge_blocks_list",
    "knowledge_block_update", "knowledge_block_split", "knowledge_block_merge",
    "cloze_generate", "recitation_evaluate", "memory_summary",
    "memory_questions_generate", "memory_workbook_export",
    "cloze_submit", "memory_questions_submit", "student_dashboard",
    "recitation_book_export", "wrong_question_book_export",
    "question_bank_import",
}
TEACHER_ACTIONS = {
    "shared_course_create", "teacher_document_upload", "teacher_document_delete",
    "document_status", "course_knowledge_status", "class_question_analysis",
    "class_weak_point_analysis", "uncovered_question_analysis",
    "class_quiz_analysis", "teaching_report", "class_data_export",
}


@dataclass
class AgentRequest:
    request_id: str
    agent: str
    action: str
    actor: dict[str, str]
    scope: dict[str, Any] = field(default_factory=dict)
    input: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AgentRequest":
        return cls(**{key: value.get(key, {} if key in {"scope", "input", "context"} else "")
                     for key in ("request_id", "agent", "action", "actor", "scope", "input", "context")})


@dataclass
class AgentResponse:
    request_id: str
    status: str
    data: Any = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = {"request_id": self.request_id, "status": self.status}
        if self.message:
            result["message"] = self.message
        if self.data is not None:
            result["data"] = self.data
        return result


class CampusAgentService:
    """Unified, adapter-independent entry point for both allowed agents."""

    def __init__(self, campus: CampusService):
        self.campus = campus
        self.memory = MemoryLearningSkill(campus)

    def invoke(self, request: AgentRequest | dict[str, Any]) -> AgentResponse:
        req = AgentRequest.from_dict(request) if isinstance(request, dict) else request
        try:
            if req.agent not in {"student_assistant", "teacher_assistant"}:
                raise ValidationError("Agent 不合法")
            if req.agent == "teacher_assistant" and not TEACHER_PORTAL_ENABLED:
                return AgentResponse(req.request_id, "disabled", message="教师端当前已禁用，学生端最小闭环稳定后再开放")
            actions = STUDENT_ACTIONS if req.agent == "student_assistant" else TEACHER_ACTIONS
            expected_role = "student" if req.agent == "student_assistant" else "teacher"
            if req.action not in actions:
                return AgentResponse(req.request_id, "not_implemented", message="该功能暂未实现")
            user_id = str(req.actor.get("user_id", "")).strip()
            role = str(req.actor.get("role", "")).strip()
            if not user_id or role != expected_role:
                raise ValidationError("用户角色不合法")
            data = self._dispatch(req, user_id, role)
            return AgentResponse(req.request_id, "success", data=data)
        except CampusError as exc:
            return AgentResponse(req.request_id, "error", message=str(exc))
        except (KeyError, TypeError, ValueError) as exc:
            return AgentResponse(req.request_id, "error", message=f"输入参数不完整或格式错误：{exc}")
        except Exception as exc:
            return AgentResponse(req.request_id, "error", message=f"智能体执行失败：{exc}")

    def _upload(self, req: AgentRequest, user_id: str, role: str) -> dict:
        encoded = req.input.get("content_base64")
        if not encoded:
            raise ValidationError("必须提供受控文件内容 content_base64")
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise ValidationError("content_base64 格式无效") from exc
        return self.campus.upload_document(req.scope["course_id"], user_id, role,
                                           req.input["file_name"], req.input["mime_type"], data)

    def _dispatch(self, req: AgentRequest, user_id: str, role: str) -> Any:
        action, inp, scope = req.action, req.input, req.scope
        if action == "personal_course_create":
            return self.campus.create_course(inp["course_name"], "personal_course", user_id, role, inp.get("description", ""))
        if action == "personal_course_delete":
            self.campus.delete_personal_course(scope["course_id"], user_id)
            return {"deleted": True}
        if action == "shared_course_create":
            return self.campus.create_course(inp["course_name"], "shared_course", user_id, role,
                                             inp.get("description", ""), inp.get("visibility", "enrolled"))
        if action == "available_courses_list":
            return self.campus.list_courses(user_id, role)
        if action == "course_select":
            return self.campus.require_access(scope["course_id"], user_id, role)
        if action in {"student_document_upload", "teacher_document_upload"}:
            return self._upload(req, user_id, role)
        if action in {"student_document_delete", "teacher_document_delete"}:
            self.campus.delete_document(inp["document_id"], user_id, role); return {"deleted": True}
        if action == "document_status":
            return self.campus.list_documents(scope["course_id"], user_id, role)
        if action == "course_knowledge_status":
            return self.campus.knowledge_status(scope["course_id"], user_id, role)
        if action == "course_qa":
            return self.campus.ask(scope["course_id"], user_id, role, inp["question"])
        if action == "quiz_generate":
            return self.campus.generate_quiz(scope["course_id"], user_id, role, inp.get("question_id"))
        if action == "quiz_submit":
            return self.campus.submit_quiz(scope["course_id"], user_id, role, inp.get("question_id"), inp["items"], inp["responses"])
        if action in {"learning_profile", "wrong_question_list", "weak_point_analysis"}:
            profile = self.campus.profile(scope["course_id"], user_id, role)
            return profile if action == "learning_profile" else profile["wrong_questions" if action == "wrong_question_list" else "weak_points"]
        if action == "personal_data_export":
            return {"file_name": "personal_learning.csv", "content_base64": base64.b64encode(
                self.campus.export_student_csv(scope["course_id"], user_id)).decode("ascii")}
        if action == "image_text_extract":
            try:
                content = base64.b64decode(inp["content_base64"], validate=True)
            except Exception as exc:
                raise ValidationError("图片内容编码无效") from exc
            return self.memory.extract_image(scope["course_id"], user_id, inp["file_name"], inp["mime_type"], content)
        if action == "knowledge_blocks_build":
            return self.memory.build_blocks(scope["course_id"], user_id, inp.get("document_id"))
        if action == "knowledge_blocks_list":
            return self.memory.list_blocks(scope["course_id"], user_id)
        if action == "knowledge_block_update":
            return self.memory.update_block(int(inp["block_id"]), user_id, inp["title"], inp.get("keywords", []),
                                            inp["content"], inp.get("favorite"))
        if action == "knowledge_block_split":
            return self.memory.split_block(int(inp["block_id"]), user_id, int(inp["position"]))
        if action == "knowledge_block_merge":
            return self.memory.merge_next(int(inp["block_id"]), user_id)
        if action == "cloze_generate":
            return self.memory.cloze(int(inp["block_id"]), user_id, inp.get("extra_keywords", []))
        if action == "cloze_submit":
            return self.memory.submit_cloze(int(inp["block_id"]), user_id, inp.get("extra_keywords", []), inp["responses"])
        if action == "recitation_evaluate":
            return self.memory.evaluate_recitation(int(inp["block_id"]), user_id, inp["recited_text"])
        if action == "memory_summary":
            return self.memory.memory_summary(scope["course_id"], user_id)
        if action == "memory_questions_generate":
            return self.memory.generate_questions(scope["course_id"], user_id, int(inp.get("count", 6)))
        if action == "question_bank_import":
            try:
                content = base64.b64decode(inp["content_base64"], validate=True)
            except Exception as exc:
                raise ValidationError("题库内容编码无效") from exc
            return self.memory.import_question_bank(scope["course_id"], user_id, inp["file_name"],
                                                    inp.get("mime_type", "application/octet-stream"), content)
        if action == "memory_questions_submit":
            return self.memory.grade_questions(scope["course_id"], user_id, inp["questions"], inp["responses"])
        if action == "student_dashboard":
            return self.memory.student_dashboard(user_id, scope.get("course_id"))
        if action == "recitation_book_export":
            content = self.memory.export_recitation_book(scope["course_id"], user_id, inp.get("course_name", "课程"))
            return {"file_name":"recitation_book.docx","content_base64":base64.b64encode(content).decode("ascii")}
        if action == "wrong_question_book_export":
            content = self.memory.export_wrong_question_book(scope["course_id"], user_id, inp.get("course_name", "课程"))
            return {"file_name":"wrong_question_book.docx","content_base64":base64.b64encode(content).decode("ascii")}
        if action == "memory_workbook_export":
            content = self.memory.export_workbook(inp.get("course_name", "课程"), inp["questions"])
            return {"file_name": "memory_workbook.docx", "content_base64": base64.b64encode(content).decode("ascii")}
        if action in {"class_question_analysis", "class_weak_point_analysis", "uncovered_question_analysis", "class_quiz_analysis"}:
            result = self.campus.class_analysis(scope["course_id"], user_id)
            keys = {"class_question_analysis":"frequent_questions", "class_weak_point_analysis":"weak_points",
                    "uncovered_question_analysis":"uncovered_questions"}
            return result if action == "class_quiz_analysis" else result[keys[action]]
        if action == "teaching_report":
            return self.campus.teaching_report(scope["course_id"], user_id)
        if action == "class_data_export":
            file_format = str(inp.get("format", "csv")).lower()
            exporters = {"csv": self.campus.export_class_csv, "xlsx": self.campus.export_class_excel,
                         "docx": self.campus.export_class_word}
            if file_format not in exporters:
                raise ValidationError("导出格式仅支持 csv、xlsx 或 docx")
            content = exporters[file_format](scope["course_id"], user_id)
            return {"file_name": f"class_report.{file_format}",
                    "content_base64": base64.b64encode(content).decode("ascii")}
        return {"status": "not_implemented", "message": "该功能暂未实现"}
