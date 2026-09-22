from pydantic import BaseModel

from campus_service import CampusService
from skills.contracts import DictOutput, ProjectSkill, SkillContext


class QuizGenerationInput(BaseModel):
    question_id: int | None = None


class QuizGenerationSkill(ProjectSkill[QuizGenerationInput, DictOutput]):
    name = "quiz_generation_skill"

    def __init__(self, campus: CampusService):
        super().__init__(); self.campus = campus

    def execute(self, context: SkillContext, payload: QuizGenerationInput) -> DictOutput:
        return DictOutput(data=self.campus.generate_quiz(
            context.course_id, context.user_id, context.role, payload.question_id,
        ))
