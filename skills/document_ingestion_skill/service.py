import base64

from pydantic import BaseModel, Field

from campus_service import CampusService, ValidationError
from skills.contracts import DictOutput, ProjectSkill, SkillContext


class DocumentIngestionInput(BaseModel):
    file_name: str = Field(min_length=1, max_length=180)
    mime_type: str = Field(min_length=1, max_length=120)
    content_base64: str = Field(min_length=1)


class DocumentIngestionSkill(ProjectSkill[DocumentIngestionInput, DictOutput]):
    name = "document_ingestion_skill"

    def __init__(self, campus: CampusService):
        super().__init__(); self.campus = campus

    def execute(self, context: SkillContext, payload: DocumentIngestionInput) -> DictOutput:
        try:
            content = base64.b64decode(payload.content_base64, validate=True)
        except Exception as exc:
            raise ValidationError("上传内容编码无效") from exc
        return DictOutput(data=self.campus.upload_document(
            context.course_id, context.user_id, context.role,
            payload.file_name, payload.mime_type, content,
        ))
