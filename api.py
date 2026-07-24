from __future__ import annotations

import os
from datetime import date

from fastapi import Cookie, Depends, FastAPI, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from agent_service import CampusAgentService
from auth_service import AuthService
from campus_service import CampusError, CampusService
from config import DB_PATH, MATERIALS_DIR, MAX_UPLOAD_BYTES, MAX_UPLOAD_MB, student_import_config_status
from database import LearningDatabase
from llm_provider import backend_provider_status
from ingestion_service import IngestionService
from question_bank_service import QuestionBankService
from teacher_service import TeacherService

db = LearningDatabase(DB_PATH)
campus = CampusService(db)
campus.seed_demo(MATERIALS_DIR)
agents = CampusAgentService(campus)
auth = AuthService(db)
teachers = TeacherService(db, campus)
ingestion = IngestionService(db, campus)
question_banks = QuestionBankService(db, campus)
app = FastAPI(title="智教伴学 API", version="1.0.0")
allowed_origins = [value.strip() for value in os.environ.get(
    "ZHIJIAO_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",") if value.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


class AgentPayload(BaseModel):
    request_id: str
    agent: str
    action: str
    actor: dict
    scope: dict = {}
    input: dict = {}
    context: dict = {}


class LoginPayload(BaseModel):
    username: str
    password: str


class CourseCreatePayload(BaseModel):
    course_name: str
    description: str = ""


class TermCreatePayload(BaseModel):
    term_name: str
    starts_on: date | None = None
    ends_on: date | None = None


class ClassCreatePayload(BaseModel):
    course_id: str
    term_id: str
    class_name: str


class MemberImportPayload(BaseModel):
    students: list[dict] = Field(min_length=1, max_length=5000)


class ChangePasswordPayload(BaseModel):
    old_password: str
    new_password: str


class BlockReviewPayload(BaseModel):
    markdown: str = ""
    plain_text: str = ""
    latex: str = ""
    visibility_level: str = "PUBLIC"
    accepted: bool = True


class SourceVisibilityPayload(BaseModel):
    visible: bool


class ClassificationPayload(BaseModel):
    destination: str
    semantic_role: str = ""
    question_group_key: str = ""
    reason: str = "教师手动调整"


class QuestionReviewPayload(BaseModel):
    question_type: str | None = None
    stem_markdown: str | None = None
    answer_markdown: str | None = None
    explanation_markdown: str | None = None
    knowledge_points: list[str] | None = None
    status: str | None = None
    knowledge_node_id: str | None = None
    options: list[dict] | None = None
    correct_answer: str | list[str] | None = None
    difficulty: str | None = None
    duration_seconds: int | None = None


class QuestionBankSubmitPayload(BaseModel):
    version_id: str
    responses: list[dict] = Field(min_length=1, max_length=100)


class QuestionFolderPayload(BaseModel):
    folder_name: str
    folder_type: str


class QuestionMovePayload(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=500)
    folder_id: str | None = None


class QuestionBulkReviewPayload(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=500)
    status: str = "approved"


class SemanticAnalysisPayload(BaseModel):
    analysis_mode: str = "api"
    ai_provider: str = "openai_compatible"
    ai_base_url: str = ""
    ai_model: str = ""
    ai_api_key: str = ""


class KnowledgeNodePayload(BaseModel):
    title: str | None = None
    markdown: str | None = None
    keywords: list[str] | None = None
    parent_id: str | None = None
    sort_order: int | None = None
    status: str | None = None
    reason: str | None = None


class MaterialMetadataPayload(BaseModel):
    material_type: str
    tags: list[str] = Field(default_factory=list)


class BatchDeletePayload(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)


class KnowledgeMergePayload(BaseModel):
    node_ids: list[str] = Field(min_length=2)
    title: str


class KnowledgeSplitPayload(BaseModel):
    node_id: str
    parts: list[dict] = Field(min_length=2)


class RelationReviewPayload(BaseModel):
    relation_id: str
    status: str


def current_user(token: str = Depends(oauth2_scheme)) -> dict:
    try:
        return auth.authenticate(token)
    except CampusError as exc:
        raise HTTPException(status_code=401, detail=str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc


def current_teacher(user: dict = Depends(current_user)) -> dict:
    if user.get("role") != "teacher":
        raise HTTPException(status_code=403, detail="仅教师可以访问该接口")
    if user.get("must_change_password"):
        raise HTTPException(status_code=403, detail="请先修改初始密码")
    return user


def current_student(user: dict = Depends(current_user)) -> dict:
    if user.get("role") != "student":
        raise HTTPException(status_code=403, detail="仅学生可以访问该接口")
    if user.get("must_change_password"):
        raise HTTPException(status_code=403, detail="请先修改初始密码")
    return user


def current_ready_user(user: dict = Depends(current_user)) -> dict:
    if user.get("must_change_password"):
        raise HTTPException(status_code=403, detail="请先修改初始密码")
    return user


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        "zhijiao_refresh", token, httponly=True, secure=os.environ.get("ZHIJIAO_COOKIE_SECURE") == "1",
        samesite="lax", max_age=7 * 24 * 3600, path="/api/v1/auth",
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "ai_agent": backend_provider_status()}


@app.get("/api/v1/system/capabilities")
def capabilities() -> dict:
    return {"max_upload_bytes": MAX_UPLOAD_BYTES, "max_upload_mb": MAX_UPLOAD_MB}


@app.get("/api/v1/system/student-import-config")
def student_import_config(user: dict = Depends(current_teacher)) -> dict:
    return student_import_config_status()


@app.get("/api/v1/system/parser-status")
def parser_status(user: dict = Depends(current_teacher)) -> dict:
    return ingestion.parser_status()


@app.post("/api/v1/auth/login")
def login(payload: LoginPayload, response: Response) -> dict:
    try:
        user, access_token, refresh_token = auth.login(payload.username, payload.password)
        _set_refresh_cookie(response, refresh_token)
        return {"access_token": access_token, "token_type": "bearer", "expires_in": 900, "user": user}
    except CampusError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.post("/api/v1/auth/refresh")
def refresh(response: Response, zhijiao_refresh: str | None = Cookie(default=None)) -> dict:
    if not zhijiao_refresh:
        raise HTTPException(status_code=401, detail="缺少刷新令牌")
    try:
        user, access_token, refresh_token = auth.refresh(zhijiao_refresh)
        _set_refresh_cookie(response, refresh_token)
        return {"access_token": access_token, "token_type": "bearer", "expires_in": 900, "user": user}
    except CampusError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.post("/api/v1/auth/logout")
def logout(response: Response, zhijiao_refresh: str | None = Cookie(default=None)) -> dict:
    if zhijiao_refresh:
        auth.revoke(zhijiao_refresh)
    response.delete_cookie("zhijiao_refresh", path="/api/v1/auth")
    return {"logged_out": True}


@app.get("/api/v1/auth/me")
def me(user: dict = Depends(current_user)) -> dict:
    return user


@app.post("/api/v1/auth/change-password")
def change_password(payload: ChangePasswordPayload, response: Response,
                    user: dict = Depends(current_user)) -> dict:
    try:
        updated, access_token, refresh_token = auth.change_password(
            user, payload.old_password, payload.new_password
        )
        _set_refresh_cookie(response, refresh_token)
        return {"access_token": access_token, "token_type": "bearer", "expires_in": 900, "user": updated}
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/teacher/courses")
def teacher_courses(user: dict = Depends(current_teacher)) -> list[dict]:
    return teachers.list_courses(user)


@app.post("/api/v1/teacher/courses", status_code=201)
def teacher_course_create(payload: CourseCreatePayload, user: dict = Depends(current_teacher)) -> dict:
    try:
        return teachers.create_course(user, payload.course_name, payload.description)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/teacher/terms")
def teacher_terms(user: dict = Depends(current_teacher)) -> list[dict]:
    return teachers.list_terms(user)


@app.post("/api/v1/teacher/terms", status_code=201)
def teacher_term_create(payload: TermCreatePayload, user: dict = Depends(current_teacher)) -> dict:
    try:
        return teachers.create_term(user, payload.term_name, payload.starts_on, payload.ends_on)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/teacher/classes")
def teacher_classes(course_id: str | None = None, user: dict = Depends(current_teacher)) -> list[dict]:
    return teachers.list_classes(user, course_id)


@app.post("/api/v1/teacher/classes", status_code=201)
def teacher_class_create(payload: ClassCreatePayload, user: dict = Depends(current_teacher)) -> dict:
    try:
        return teachers.create_class(user, payload.course_id, payload.term_id, payload.class_name)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/teacher/classes/{class_id}/members")
def teacher_class_members(class_id: str, user: dict = Depends(current_teacher)) -> list[dict]:
    try:
        return teachers.list_members(user, class_id)
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/api/v1/teacher/classes/{class_id}/members/import")
def teacher_class_member_import(class_id: str, payload: MemberImportPayload,
                                user: dict = Depends(current_teacher)) -> dict:
    try:
        return teachers.import_members(user, class_id, payload.students)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/teacher/classes/{class_id}/members/import-file")
async def teacher_class_member_file_import(class_id: str, file: UploadFile = File(...),
                                           user: dict = Depends(current_teacher)) -> dict:
    data = await file.read(6 * 1024 * 1024)
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="名单文件不能超过 5MB")
    try:
        return teachers.import_member_file(user, class_id, file.filename or "members.csv", data)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/student/courses")
def student_courses(user: dict = Depends(current_student)) -> list[dict]:
    return campus.list_courses(str(user["user_id"]), "student")


@app.post("/api/v1/teacher/courses/{course_id}/documents", status_code=202)
async def teacher_document_upload(course_id: str, file: UploadFile = File(...),
                                  analysis_mode: str = Form("api"),
                                  ai_provider: str = Form("openai_compatible"),
                                  ai_base_url: str = Form(""),
                                  ai_model: str = Form(""),
                                  ai_api_key: str = Form(""),
                                  user: dict = Depends(current_teacher)) -> dict:
    try:
        await file.seek(0)
        job = await run_in_threadpool(
            ingestion.queue_document_stream,
            user, course_id, file.filename or "document",
            file.content_type or "application/octet-stream", file.file,
            analysis_mode=analysis_mode,
            ai_settings={
                "provider": ai_provider, "base_url": ai_base_url,
                "model": ai_model, "api_key": ai_api_key,
            },
        )
        return job
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/teacher/courses/{course_id}/ingestion-jobs")
def teacher_ingestion_jobs(course_id: str, user: dict = Depends(current_teacher)) -> list[dict]:
    try:
        return ingestion.list_jobs(user, course_id)
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.patch("/api/v1/teacher/documents/{document_id}/material-metadata")
def teacher_document_material_metadata(document_id: str, payload: MaterialMetadataPayload,
                                       user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.update_material_metadata(
            user, document_id, material_type=payload.material_type, tags=payload.tags
        )
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/v1/teacher/documents/{document_id}")
def teacher_document_delete(document_id: str, user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.delete_document(user, document_id)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/teacher/documents/batch-delete")
def teacher_documents_batch_delete(payload: BatchDeletePayload,
                                   user: dict = Depends(current_teacher)) -> dict:
    return ingestion.delete_documents(user, payload.ids)


@app.get("/api/v1/teacher/ingestion-jobs/{job_id}")
def teacher_ingestion_job(job_id: str, user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.get_job(user, job_id)
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/api/v1/teacher/ingestion-jobs/{job_id}/cancel")
def teacher_ingestion_cancel(job_id: str, user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.cancel_job(user, job_id)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/teacher/ingestion-jobs/{job_id}/retry")
def teacher_ingestion_retry(job_id: str, user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.retry_job(user, job_id)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/teacher/courses/{course_id}/knowledge-health")
def teacher_knowledge_health(course_id: str, user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.course_health(user, course_id)
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get("/api/v1/teacher/courses/{course_id}/publish-readiness")
def teacher_publish_readiness(course_id: str, user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.publish_readiness(user, course_id)
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get("/api/v1/teacher/courses/{course_id}/teaching-overview")
def teacher_teaching_overview(course_id: str, class_id: str | None = None,
                              user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.teaching_overview(user, course_id, class_id=class_id)
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get("/api/v1/teacher/documents/{document_id}/blocks")
def teacher_document_blocks(document_id: str, user: dict = Depends(current_teacher)) -> list[dict]:
    try:
        return ingestion.list_blocks(user, document_id)
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/api/v1/teacher/documents/{document_id}/semantic-analysis", status_code=202)
def teacher_semantic_analysis(document_id: str,
                              payload: SemanticAnalysisPayload = SemanticAnalysisPayload(),
                              user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.queue_semantic_analysis(
            user, document_id, analysis_mode=payload.analysis_mode,
            ai_settings={
                "provider": payload.ai_provider, "base_url": payload.ai_base_url,
                "model": payload.ai_model, "api_key": payload.ai_api_key,
            },
        )
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/teacher/documents/{document_id}/semantic-analysis/latest")
def teacher_latest_semantic_analysis(document_id: str, user: dict = Depends(current_teacher)) -> dict | None:
    try:
        return ingestion.latest_analysis_for_document(user, document_id)
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get("/api/v1/teacher/analysis-jobs/{analysis_job_id}")
def teacher_analysis_job(analysis_job_id: str, user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.get_analysis_job(user, analysis_job_id)
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/api/v1/teacher/analysis-jobs/{analysis_job_id}/retry")
def teacher_analysis_retry(analysis_job_id: str, user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.retry_analysis(user, analysis_job_id)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/teacher/analysis-jobs/{analysis_job_id}/cancel")
def teacher_analysis_cancel(analysis_job_id: str, user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.cancel_analysis(user, analysis_job_id)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/teacher/documents/{document_id}/outline")
def teacher_document_outline(document_id: str, user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.document_outline(user, document_id)
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get("/api/v1/teacher/courses/{course_id}/outline")
def teacher_course_outline(course_id: str, user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.course_outline(user, course_id)
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.patch("/api/v1/teacher/knowledge-nodes/{node_id}")
def teacher_knowledge_node(node_id: str, payload: KnowledgeNodePayload,
                           user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.update_node(user, node_id, payload.model_dump(exclude_none=True))
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/teacher/knowledge-nodes/merge")
def teacher_knowledge_merge(payload: KnowledgeMergePayload, user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.merge_nodes(user, payload.node_ids, payload.title)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/teacher/knowledge-nodes/split")
def teacher_knowledge_split(payload: KnowledgeSplitPayload, user: dict = Depends(current_teacher)) -> list[dict]:
    try:
        return ingestion.split_node(user, payload.node_id, payload.parts)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/teacher/courses/{course_id}/knowledge-trash")
def teacher_knowledge_trash(course_id: str, user: dict = Depends(current_teacher)) -> list[dict]:
    try:
        return ingestion.list_trash(user, course_id)
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/api/v1/teacher/knowledge-trash/{node_id}/restore")
def teacher_knowledge_trash_restore(node_id: str, user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.restore_trash_node(user, node_id)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/v1/teacher/knowledge-trash/{node_id}")
def teacher_knowledge_trash_delete(node_id: str, user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.permanently_delete_trash_node(user, node_id)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/teacher/knowledge-trash/batch-delete")
def teacher_knowledge_trash_batch_delete(payload: BatchDeletePayload,
                                         user: dict = Depends(current_teacher)) -> dict:
    return ingestion.permanently_delete_trash_nodes(user, payload.ids)


@app.get("/api/v1/teacher/courses/{course_id}/knowledge-relations")
def teacher_knowledge_relations(course_id: str, user: dict = Depends(current_teacher)) -> list[dict]:
    try:
        return ingestion.list_relations(user, course_id)
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.patch("/api/v1/teacher/courses/{course_id}/knowledge-relations")
def teacher_knowledge_relation_review(course_id: str, payload: RelationReviewPayload,
                                      user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.review_relation(user, course_id, payload.relation_id, payload.status)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/v1/teacher/documents/{document_id}/student-visibility")
def teacher_document_visibility(document_id: str, payload: SourceVisibilityPayload,
                                user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.set_student_file_visibility(user, document_id, payload.visible)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/student/courses/{course_id}/documents")
def student_course_documents(course_id: str, user: dict = Depends(current_student)) -> list[dict]:
    try:
        return ingestion.list_student_source_files(user, course_id)
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/api/v1/documents/{document_id}/preview-token")
def document_preview_token(document_id: str, user: dict = Depends(current_ready_user)) -> dict:
    try:
        document = ingestion.require_document_access(user, document_id)
        token = auth.issue_document_token(user, document_id)
        descriptor = ingestion.preview_descriptor(user, document_id)
        return {
            **descriptor,
            "preview_url": f"/api/v1/documents/{document_id}/preview?token={token}",
            "download_url": f"/api/v1/documents/{document_id}/source?token={token}",
            "mime_type": document["mime_type"],
            "original_name": document["original_name"],
            "expires_in": auth.document_token_minutes * 60,
        }
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get("/api/v1/documents/{document_id}/source")
def document_source(document_id: str, token: str = Query(...)) -> FileResponse:
    try:
        user = auth.authenticate_document_token(token, document_id)
        document, source = ingestion.source_file(user, document_id)
        return FileResponse(
            source, media_type=document["mime_type"], filename=document["original_name"],
            content_disposition_type="attachment",
        )
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get("/api/v1/documents/{document_id}/preview")
def document_preview(document_id: str, token: str = Query(...)) -> Response:
    try:
        user = auth.authenticate_document_token(token, document_id)
        media_type, value = ingestion.preview_file(user, document_id)
        if isinstance(value, str):
            return Response(content=value, media_type=f"{media_type}; charset=utf-8")
        return FileResponse(
            value, media_type=media_type, filename="preview.pdf", content_disposition_type="inline",
        )
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.patch("/api/v1/teacher/blocks/{block_id}/review")
def teacher_block_review(block_id: str, payload: BlockReviewPayload,
                         user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.review_block(user, block_id, **payload.model_dump())
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/teacher/blocks/{block_id}/classification")
def teacher_block_classification(block_id: str, user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.get_classification(user, block_id)
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.patch("/api/v1/teacher/blocks/{block_id}/classification")
def teacher_block_classification_update(block_id: str, payload: ClassificationPayload,
                                        user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.update_classification(user, block_id, **payload.model_dump())
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/teacher/courses/{course_id}/question-bank")
def teacher_question_bank(course_id: str, user: dict = Depends(current_teacher)) -> list[dict]:
    try:
        return ingestion.list_question_bank(user, course_id)
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/api/v1/teacher/courses/{course_id}/question-bank/import")
async def teacher_question_bank_import(course_id: str, file: UploadFile = File(...),
                                       folder_id: str = Form(""),
                                       ai_mode: str = Form("auto"),
                                       ai_provider: str = Form("openai_compatible"),
                                       ai_base_url: str = Form(""),
                                       ai_model: str = Form(""),
                                       ai_api_key: str = Form(""),
                                       user: dict = Depends(current_teacher)) -> dict:
    data = await file.read(20 * 1024 * 1024 + 1)
    try:
        return question_banks.import_template(
            user, course_id, file.filename or "question-bank.xlsx",
            file.content_type or "application/octet-stream", data,
            ai_mode=ai_mode,
            ai_settings={
                "provider": ai_provider,
                "base_url": ai_base_url,
                "model": ai_model,
                "api_key": ai_api_key,
            },
            folder_id=folder_id or None,
        )
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/teacher/courses/{course_id}/question-bank/imports")
def teacher_question_bank_imports(course_id: str,
                                  user: dict = Depends(current_teacher)) -> list[dict]:
    try:
        return question_banks.list_imports(user, course_id)
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get("/api/v1/teacher/courses/{course_id}/question-folders")
def teacher_question_folders(course_id: str, user: dict = Depends(current_teacher)) -> list[dict]:
    try:
        return question_banks.list_folders(user, course_id)
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/api/v1/teacher/courses/{course_id}/question-folders", status_code=201)
def teacher_question_folder_create(course_id: str, payload: QuestionFolderPayload,
                                   user: dict = Depends(current_teacher)) -> dict:
    try:
        return question_banks.create_folder(
            user, course_id, payload.folder_name, payload.folder_type
        )
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/teacher/courses/{course_id}/question-bank/move")
def teacher_question_move(course_id: str, payload: QuestionMovePayload,
                          user: dict = Depends(current_teacher)) -> dict:
    try:
        return question_banks.move_items(user, course_id, payload.item_ids, payload.folder_id)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/teacher/courses/{course_id}/question-bank/bulk-review")
def teacher_question_bulk_review(course_id: str, payload: QuestionBulkReviewPayload,
                                 user: dict = Depends(current_teacher)) -> dict:
    if payload.status not in {"approved", "rejected", "draft"}:
        raise HTTPException(status_code=400, detail="批量审核状态无效")
    succeeded, failed = [], []
    for item_id in list(dict.fromkeys(payload.item_ids)):
        try:
            ingestion.review_question(user, item_id, {"status": payload.status})
            succeeded.append(item_id)
        except CampusError as exc:
            failed.append({"item_id": item_id, "message": str(exc)})
    return {"succeeded": succeeded, "failed": failed}


@app.patch("/api/v1/teacher/question-bank/{item_id}")
def teacher_question_review(item_id: str, payload: QuestionReviewPayload,
                            user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.review_question(user, item_id, payload.model_dump(exclude_none=True))
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/teacher/courses/{course_id}/question-bank/publish")
def teacher_question_publish(course_id: str, folder_id: str | None = None,
                             user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.publish_question_bank(user, course_id, folder_id=folder_id)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/teacher/courses/{course_id}/question-bank/statistics")
def teacher_question_statistics(course_id: str, class_id: str | None = Query(default=None),
                                folder_id: str | None = Query(default=None),
                                user: dict = Depends(current_teacher)) -> dict:
    try:
        return question_banks.statistics(user, course_id, class_id, folder_id)
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get("/api/v1/student/courses/{course_id}/question-bank")
def student_question_bank(course_id: str, limit: int = Query(default=30, ge=1, le=100),
                          offset: int = Query(default=0, ge=0),
                          folder_id: str | None = Query(default=None),
                          user: dict = Depends(current_student)) -> dict:
    try:
        return question_banks.student_questions(
            user, course_id, limit=limit, offset=offset, folder_id=folder_id
        )
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get("/api/v1/student/courses/{course_id}/question-folders")
def student_question_folders(course_id: str,
                             user: dict = Depends(current_student)) -> list[dict]:
    try:
        return question_banks.student_publications(user, course_id)
    except CampusError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/api/v1/student/courses/{course_id}/question-bank/submit")
def student_question_submit(course_id: str, payload: QuestionBankSubmitPayload,
                            user: dict = Depends(current_student)) -> dict:
    try:
        return question_banks.submit(user, course_id, payload.version_id, payload.responses)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/teacher/courses/{course_id}/knowledge-versions/publish")
def teacher_knowledge_publish(course_id: str, user: dict = Depends(current_teacher)) -> dict:
    try:
        return ingestion.publish(user, course_id)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/agent/invoke")
def invoke(payload: AgentPayload, user: dict = Depends(current_ready_user)) -> dict:
    if payload.actor.get("user_id") != user["user_id"] or payload.actor.get("role") != user["role"]:
        raise HTTPException(status_code=403, detail="Agent actor 与登录身份不一致")
    return agents.invoke(payload.model_dump()).to_dict()


@app.post("/api/v1/documents/upload")
async def upload_document(course_id: str = Form(...), user_id: str = Form(...), role: str = Form(...),
                          file: UploadFile = File(...), user: dict = Depends(current_ready_user)) -> dict:
    if user_id != user["user_id"] or role != user["role"]:
        raise HTTPException(status_code=403, detail="上传身份与登录身份不一致")
    try:
        return campus.upload_document(course_id, user_id, role, file.filename or "document",
                                      file.content_type or "application/octet-stream", await file.read())
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/courses")
def list_courses(user_id: str, role: str, user: dict = Depends(current_ready_user)) -> list[dict]:
    if user_id != user["user_id"] or role != user["role"]:
        raise HTTPException(status_code=403, detail="查询身份与登录身份不一致")
    try:
        return campus.list_courses(user_id, role)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
