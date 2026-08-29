from typing import Literal

from pydantic import BaseModel, Field

from campus_service import CampusService
from skills.contracts import DictOutput, ProjectSkill, SkillContext


class CourseQAInput(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    intent: Literal["start", "respond", "hint", "reveal", "end"] = "start"
    student_message: str = Field(default="", max_length=2000)
    session_id: str | None = Field(default=None, max_length=100)
    retrieval_scope: Literal["all", "material"] = "all"
    material_type: str | None = Field(default=None, max_length=64)


class CourseQASkill(ProjectSkill[CourseQAInput, DictOutput]):
    name = "course_qa_skill"

    def __init__(self, campus: CampusService):
        super().__init__(); self.campus = campus

    def execute(self, context: SkillContext, payload: CourseQAInput) -> DictOutput:
        return DictOutput(data=self.campus.ask(
            context.course_id, context.user_id, context.role, payload.question,
            intent=payload.intent, student_message=payload.student_message,
            session_id=payload.session_id,
            retrieval_scope=payload.retrieval_scope,
            material_type=payload.material_type,
        ))
