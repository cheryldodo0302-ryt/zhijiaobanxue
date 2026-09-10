from pydantic import BaseModel, Field

from campus_service import CampusService
from skills.contracts import DictOutput, ProjectSkill, SkillContext


class KnowledgeRetrievalInput(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=4, ge=1, le=20)


class KnowledgeRetrievalSkill(ProjectSkill[KnowledgeRetrievalInput, DictOutput]):
    name = "knowledge_retrieval_skill"

    def __init__(self, campus: CampusService):
        super().__init__(); self.campus = campus

    def execute(self, context: SkillContext, payload: KnowledgeRetrievalInput) -> DictOutput:
        self.campus.require_access(context.course_id, context.user_id, context.role)
        evidence = self.campus._retriever(context.course_id).search(payload.question, payload.top_k)
        return DictOutput(data={"sources": [item.to_dict() for item in evidence]})
