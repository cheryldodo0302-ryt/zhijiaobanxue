from __future__ import annotations

import base64
import os
import re
import socket
import subprocess
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
API_PORT = 18000
WEB_PORT = 15173


def wait_port(port: int, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.3)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise RuntimeError(f"端口 {port} 启动超时")


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
    else:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def credentials(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    teacher = re.search(r"教师：demo_teacher\s+密码：(\S+)", text)
    student = re.search(r"学生：demo_student\s+密码：(\S+)", text)
    if not teacher or not student:
        raise RuntimeError("演示账号文件格式错误")
    return teacher.group(1), student.group(1)


def main() -> int:
    data_dir = Path(os.environ.get("ZHIJIAO_DATA_DIR", ROOT / ".e2e-data")).resolve()
    env = os.environ.copy()
    env["ZHIJIAO_DATA_DIR"] = str(data_dir)
    env["ZHIJIAO_AI_MODE"] = "mock"
    env["ZHIJIAO_AI_PROVIDER"] = "mock"
    for name in ("ZHIJIAO_AI_BASE_URL", "ZHIJIAO_AI_MODEL", "DASHSCOPE_API_KEY"):
        env.pop(name, None)
    # CI and desktop proxy settings must never route local E2E calls away from
    # the test servers.
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    os.environ["NO_PROXY"] = env["NO_PROXY"]
    os.environ["no_proxy"] = env["no_proxy"]
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), env.get("PYTHONPATH", "")])
    subprocess.run([sys.executable, str(ROOT / "scripts" / "bootstrap_demo.py"), "--if-empty"], env=env, check=True)
    teacher_password, student_password = credentials(data_dir / "demo_credentials.txt")
    npm = "npm.cmd" if os.name == "nt" else "npm"
    api_log = (ROOT / ".e2e-api.log").open("w", encoding="utf-8")
    web_log = (ROOT / ".e2e-web.log").open("w", encoding="utf-8")
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api:app", "--host", "127.0.0.1", "--port", str(API_PORT)],
        cwd=ROOT, env=env, stdout=api_log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    web_env = {**env, "VITE_API_PROXY_TARGET": f"http://127.0.0.1:{API_PORT}"}
    web = subprocess.Popen(
        [npm, "run", "dev", "--", "--host", "127.0.0.1", "--port", str(WEB_PORT)],
        cwd=ROOT / "web", env=web_env, stdout=web_log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    checks: list[str] = []
    try:
        wait_port(API_PORT); wait_port(WEB_PORT)
        base = f"http://127.0.0.1:{API_PORT}/api/v1"
        health = requests.get(f"http://127.0.0.1:{API_PORT}/health", timeout=5)
        assert health.status_code == 200 and health.json()["ai_agent"]["provider"] == "mock"
        checks.append("后端健康检查与 Mock 模式")
        page = requests.get(f"http://127.0.0.1:{WEB_PORT}", timeout=5)
        assert page.status_code == 200 and "<div id=\"app\"></div>" in page.text
        checks.append("前端服务")

        def login(username: str, password: str) -> tuple[requests.Session, dict]:
            session = requests.Session()
            response = session.post(f"{base}/auth/login", json={"username": username, "password": password}, timeout=8)
            assert response.status_code == 200, response.text
            data = response.json()
            session.headers["Authorization"] = f"Bearer {data['access_token']}"
            return session, data["user"]

        teacher_http, teacher = login("demo_teacher", teacher_password)
        student_http, student = login("demo_student", student_password)
        checks.append("虚构教师和学生账号登录")
        courses = student_http.get(f"{base}/student/courses", timeout=5).json()
        course = next(item for item in courses if item["course_id"] == "virtual_ai_101")
        assert course["course_id"] == "virtual_ai_101" and any(
            marker in course["course_name"] for marker in ("虚拟", "演示")
        )
        checks.append("学生课程授权隔离")

        def invoke(session: requests.Session, user: dict, agent: str, action: str, course_id: str, payload: dict):
            response = session.post(f"{base}/agent/invoke", json={
                "request_id": f"e2e_{action}_{time.time_ns()}", "agent": agent, "action": action,
                "actor": {"user_id": user["user_id"], "role": user["role"]},
                "scope": {"course_id": course_id}, "input": payload,
            }, timeout=15)
            assert response.status_code == 200, response.text
            return response.json()

        question = "为什么测试集不能参与参数选择？"
        start = invoke(student_http, student, "student_assistant", "course_qa", "virtual_ai_101", {"question": question, "intent": "start"})
        assert start["status"] == "success" and not start["data"]["can_reveal"]
        session_id = start["data"]["session_id"]
        for text in ("测试集应该只用于最后评价", "如果反复看测试结果调参就会泄漏信息"):
            turn = invoke(student_http, student, "student_assistant", "course_qa", "virtual_ai_101", {
                "question": question, "intent": "respond", "student_message": text, "session_id": session_id,
            })
        assert turn["data"]["can_reveal"]
        reveal = invoke(student_http, student, "student_assistant", "course_qa", "virtual_ai_101", {
            "question": question, "intent": "reveal", "student_message": "查看课程答案", "session_id": session_id,
        })
        assert reveal["data"]["completed"] and reveal["data"]["sources"] and reveal["data"]["question_id"]
        checks.append("服务端引导轮次、问答与证据引用")

        quiz = invoke(student_http, student, "student_assistant", "quiz_generate", "virtual_ai_101", {
            "question_id": reveal["data"]["question_id"],
        })["data"]
        responses = [item["answer"] for item in quiz["items"]]
        grade = invoke(student_http, student, "student_assistant", "quiz_submit", "virtual_ai_101", {
            "question_id": quiz["question_id"], "items": quiz["items"], "responses": responses,
        })["data"]
        assert grade["score"] == 100
        profile = invoke(student_http, student, "student_assistant", "learning_profile", "virtual_ai_101", {})["data"]
        assert profile["attempts"]
        checks.append("练习生成、提交和个人学习记录")

        refused = invoke(student_http, student, "student_assistant", "course_qa", "virtual_ai_101", {
            "question": "ZXQJ-999 FLORBAX-NEBULA", "intent": "start",
        })["data"]
        assert refused["refused"] and not refused["sources"]
        checks.append("无检索结果拒答")

        private_course = invoke(student_http, student, "student_assistant", "personal_course_create", "", {"course_name": "临时测试课"})["data"]
        bad = invoke(student_http, student, "student_assistant", "student_document_upload", private_course["course_id"], {
            "file_name": "broken.pdf", "mime_type": "application/pdf",
            "content_base64": base64.b64encode(b"not a pdf").decode("ascii"),
        })
        assert bad["status"] == "error" and "有效的 PDF" in bad["message"]
        checks.append("损坏文件拒绝")

        teacher_courses = teacher_http.get(f"{base}/teacher/courses", timeout=5).json()
        new_course = teacher_http.post(f"{base}/teacher/courses", json={"course_name": "未授权测试课", "description": ""}, timeout=5).json()
        denied = invoke(student_http, student, "student_assistant", "course_select", new_course["course_id"], {})
        assert denied["status"] == "error"
        checks.append("跨课程访问拒绝")
        overview = teacher_http.get(f"{base}/teacher/courses/virtual_ai_101/teaching-overview", timeout=8)
        assert overview.status_code == 200 and overview.json()["learning"]["quiz_count"] >= 1
        exported = invoke(teacher_http, teacher, "teacher_assistant", "class_data_export", "virtual_ai_101", {"format": "docx"})
        word = base64.b64decode(exported["data"]["content_base64"])
        with zipfile.ZipFile(BytesIO(word)) as archive:
            assert "word/document.xml" in archive.namelist()
        checks.append("教师学情和 Word 导出")
        assert teacher_courses
        print(f"E2E PASS: {len(checks)} checks")
        for check in checks:
            print(f"- {check}")
        return 0
    finally:
        stop(web); stop(api); api_log.close(); web_log.close()


if __name__ == "__main__":
    raise SystemExit(main())
