from pydantic import BaseModel

from campus_service import CampusService, ValidationError
from skills.contracts import DictOutput, ProjectSkill, SkillContext


class ClassAnalysisInput(BaseModel):
    class_id: str | None = None


class ClassAnalysisSkill(ProjectSkill[ClassAnalysisInput, DictOutput]):
    name = "class_analysis_skill"

    def __init__(self, campus: CampusService):
        super().__init__(); self.campus = campus

    def execute(self, context: SkillContext, payload: ClassAnalysisInput) -> DictOutput:
        if payload.class_id:
            raise ValidationError("当前轻量 Skill 仅支持课程级匿名聚合；教学班筛选请使用教师端教学诊断接口")
        return DictOutput(data=self.campus.class_analysis(context.course_id, context.user_id))
