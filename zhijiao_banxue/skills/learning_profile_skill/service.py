from pydantic import BaseModel

from campus_service import CampusService
from skills.contracts import DictOutput, ProjectSkill, SkillContext


class LearningProfileInput(BaseModel):
    include_history: bool = True


class LearningProfileSkill(ProjectSkill[LearningProfileInput, DictOutput]):
    name = "learning_profile_skill"

    def __init__(self, campus: CampusService):
        super().__init__(); self.campus = campus

    def execute(self, context: SkillContext, payload: LearningProfileInput) -> DictOutput:
        data = self.campus.profile(context.course_id, context.user_id, context.role)
        if not payload.include_history:
            data = {**data, "questions": [], "attempts": []}
        return DictOutput(data=data)
