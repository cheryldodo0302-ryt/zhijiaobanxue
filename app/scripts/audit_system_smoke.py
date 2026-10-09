"""Offline HTTP workflow smoke using only a disposable database and Mock AI."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with tempfile.TemporaryDirectory(prefix="zhijiao_audit_") as directory:
        os.environ["ZHIJIAO_DATA_DIR"] = directory
        os.environ["ZHIJIAO_AI_MODE"] = "mock"
        os.environ['ZHIJIAO_TEACHER_AGENT_ENABLED'] = '1'
        from fastapi.testclient import TestClient
        import api

        try:
            teacher = api.auth.create_user("audit_teacher", "audit-password-123", "teacher")
            student = api.auth.create_user("audit_student", "audit-password-123", "student")
            with TestClient(api.app) as teacher_http, TestClient(api.app) as student_http:
                for client, actor in ((teacher_http, teacher), (student_http, student)):
                    response = client.post("/api/v1/auth/login", json={"username":actor["username"], "password":"audit-password-123"})
                    assert response.status_code == 200
                    client.headers["Authorization"] = "Bearer " + response.json()["access_token"]

                def invoke(action, course_id, payload=None):
                    response = student_http.post("/api/v1/agent/invoke", json={
                        "request_id":"audit_" + action, "agent":"student_assistant", "action":action,
                        "actor":{"user_id":student["user_id"], "role":"student"},
                        "scope":{"course_id":course_id}, "input":payload or {},
                    })
                    assert response.status_code == 200
                    result = response.json()
                    assert result["status"] == "success", result.get("message")
                    return result["data"]

                created = teacher_http.post("/api/v1/teacher/courses", json={"course_name":"联通检查课程"})
                assert created.status_code == 201
                course_id = created.json()["course_id"]
                api.campus.enroll_student(course_id, teacher["user_id"], student["user_id"])
                uploaded = teacher_http.post(f"/api/v1/teacher/courses/{course_id}/documents",
                                             data={"analysis_mode":"local"},
                                             files={"file":("course.txt", "关系模型用二维表表达数据关系。".encode(), "text/plain")})
                assert uploaded.status_code == 202
                job = uploaded.json()
                api.ingestion.process_job(job["job_id"])
                analysis = api.db.fetch_one("SELECT analysis_job_id FROM semantic_analysis_jobs WHERE document_id=?", (job["document_id"],))
                if analysis:
                    api.ingestion.process_semantic_analysis(analysis["analysis_job_id"])
                assert invoke("document_status", course_id) == []
                assert teacher_http.post(f"/api/v1/teacher/documents/{job['document_id']}/knowledge-review").status_code == 200
                assert teacher_http.post(f"/api/v1/teacher/courses/{course_id}/knowledge-versions/publish").status_code == 200
                cards = invoke("knowledge_blocks_build", course_id)
                assert cards and len(invoke("knowledge_blocks_build", course_id)) == len(cards)
                qa = invoke("course_qa", course_id, {"question":"关系模型如何表达数据关系？"})
                assert qa["sources"] and not qa["refused"]
                quiz = invoke("quiz_generate", course_id, {"question_id":qa["question_id"]})
                grade = invoke("quiz_submit", course_id, {"question_id":qa["question_id"], "items":quiz["items"],
                                                          "responses":[item["answer"] for item in quiz["items"]]})
                assert grade["score"] == 100
                overview = teacher_http.get(f"/api/v1/teacher/courses/{course_id}/teaching-overview")
                assert overview.status_code == 200
                assert overview.json()["learning"]["question_count"] == 1
                print("PASS: teacher upload/review/publish -> student cited QA/cards/quiz -> teacher aggregate")

                personal = invoke("personal_course_create", "", {"course_name":"私有课程"})["course_id"]
                private_upload = student_http.post("/api/v1/documents/upload",
                                                  data={"course_id":personal, "user_id":student["user_id"], "role":"student"},
                                                  files={"file":("private.txt", "这是学生私有材料。".encode(), "text/plain")})
                assert private_upload.status_code == 200
                assert teacher_http.get(f"/api/v1/teacher/courses/{personal}/publish-readiness").status_code != 200
                assert student_http.post(f"/api/v1/teacher/courses/{course_id}/knowledge-versions/publish").status_code == 403
                print("PASS: personal course stays private; student cannot publish teacher materials")

                saved = student_http.put("/api/v1/runtime/ai-settings", json={"mode":"custom", "provider":"openai_compatible",
                                         "base_url":"https://example.com/v1", "model":"audit-model", "api_key":"audit-fake-key"})
                assert saved.status_code == 200 and "audit-fake-key" not in saved.text
                assert teacher_http.get("/api/v1/runtime/ai-settings").json()["mode"] == "mock"
                assert student_http.get("/api/v1/runtime/ai-settings").json()["model"] == "audit-model"
                assert student_http.post("/api/v1/auth/refresh").status_code == 200
                print("PASS: account AI settings isolated; key redacted; refresh cookie works")
                api.config.TEACHER_PORTAL_ENABLED = False
                assert teacher_http.get('/api/v1/teacher/courses').status_code == 403
                disabled = teacher_http.post('/api/v1/agent/invoke',json={
                    'request_id':'disabled-check','agent':'teacher_assistant','action':'class_quiz_analysis',
                    'actor':{'user_id':teacher['user_id'],'role':'teacher'},'scope':{'course_id':course_id}})
                assert disabled.status_code==403 and disabled.json()['detail']['status']=='disabled'
                assert api.agents.invoke({'request_id':'disabled-service','agent':'teacher_assistant','action':'class_quiz_analysis',
                    'actor':{'user_id':teacher['user_id'],'role':'teacher'},'scope':{'course_id':course_id}}).status=='disabled'
                api.config.TEACHER_PORTAL_ENABLED = True
                versions = teacher_http.get(f'/api/v1/teacher/courses/{course_id}/knowledge-versions').json()
                published = next(row for row in versions if row['status']=='published')
                target = f"/api/v1/teacher/courses/{course_id}/knowledge-versions/{published['version_id']}/withdraw"
                assert student_http.post(target).status_code == 403
                assert teacher_http.post(target).status_code == 200
                assert invoke('published_knowledge_list',course_id)['items']==[]
                print('PASS: teacher API/Agent share the same feature gate; explicit version withdrawal enforces roles')
        finally:
            api.db.engine.dispose()
            api.study_room.engine.dispose()


if __name__ == "__main__":
    main()
