import base64

from pydantic import BaseModel

from campus_service import CampusService
from skills.contracts import DictOutput, ProjectSkill, SkillContext


class TeachingReportInput(BaseModel):
    include_docx: bool = False


class TeachingReportSkill(ProjectSkill[TeachingReportInput, DictOutput]):
    name = "teaching_report_skill"

    def __init__(self, campus: CampusService):
        super().__init__(); self.campus = campus

    def execute(self, context: SkillContext, payload: TeachingReportInput) -> DictOutput:
        data = self.campus.teaching_report(context.course_id, context.user_id)
        if payload.include_docx:
            data = {**data, "docx_base64": base64.b64encode(
                self.campus.export_class_word(context.course_id, context.user_id)
            ).decode("ascii")}
        return DictOutput(data=data)
