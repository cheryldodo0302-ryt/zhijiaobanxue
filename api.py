from __future__ import annotations

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from agent_service import CampusAgentService
from campus_service import CampusError, CampusService
from config import DB_PATH, MATERIALS_DIR
from database import LearningDatabase
from llm_provider import backend_provider_status

db = LearningDatabase(DB_PATH)
campus = CampusService(db)
campus.seed_demo(MATERIALS_DIR)
agents = CampusAgentService(campus)
app = FastAPI(title="智教伴学 API", version="1.0.0")


class AgentPayload(BaseModel):
    request_id: str
    agent: str
    action: str
    actor: dict
    scope: dict = {}
    input: dict = {}
    context: dict = {}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "ai_agent": backend_provider_status()}


@app.post("/api/v1/agent/invoke")
def invoke(payload: AgentPayload) -> dict:
    return agents.invoke(payload.model_dump()).to_dict()


@app.post("/api/v1/documents/upload")
async def upload_document(course_id: str = Form(...), user_id: str = Form(...), role: str = Form(...),
                          file: UploadFile = File(...)) -> dict:
    try:
        return campus.upload_document(course_id, user_id, role, file.filename or "document",
                                      file.content_type or "application/octet-stream", await file.read())
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/courses")
def list_courses(user_id: str, role: str) -> list[dict]:
    try:
        return campus.list_courses(user_id, role)
    except CampusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
