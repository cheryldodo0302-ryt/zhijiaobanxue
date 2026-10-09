from pydantic import BaseModel

from campus_service import CampusService
from skills.contracts import DictOutput, ProjectSkill, SkillContext


class PermissionGuardInput(BaseModel):
    required_role: str | None = None


class PermissionGuardSkill(ProjectSkill[PermissionGuardInput, DictOutput]):
    name = "permission_guard_skill"

    def __init__(self, campus: CampusService):
        super().__init__(); self.campus = campus

    def execute(self, context: SkillContext, payload: PermissionGuardInput) -> DictOutput:
        course = self.campus.require_access(context.course_id, context.user_id, context.role)
        allowed = payload.required_role in {None, context.role}
        return DictOutput(data={"allowed": allowed, "course_id": course["course_id"]})
