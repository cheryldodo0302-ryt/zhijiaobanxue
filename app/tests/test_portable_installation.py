from pathlib import Path
import shutil

import pytest
import config
import job_secret_store as secrets
from account_ai_service import AccountAiService
from database import LearningDatabase
from scripts import bootstrap_demo


def test_copied_folder_preserves_model_key_upload_and_existing_account(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SERVER_ENV", tmp_path / "server.env")
    original = tmp_path / "original" / "data"
    original.mkdir(parents=True)
    monkeypatch.setattr(secrets, "KEY_PATH", original / ".job-secret.key")
    db = LearningDatabase(original / "learning.db")
    db.execute("INSERT INTO users(user_id,username,password_hash,role) VALUES('demo_teacher_001','demo_teacher','unchanged','teacher')")
    actor = {"user_id": "demo_teacher_001", "role": "teacher"}
    # Insert a saved configuration without DNS/network validation in this test.
    encrypted = secrets.encrypt_job_secret("test-portable-key")
    db.execute("INSERT INTO account_ai_settings(user_id,mode,provider,base_url,model,api_key_encrypted) VALUES(?,?,?,?,?,?)",
               (actor["user_id"], "custom", "openai_compatible", "https://example.com/v1", "chosen-model", encrypted))
    upload = original / "uploads" / "course" / "material.txt"
    upload.parent.mkdir(parents=True)
    upload.write_text("already uploaded content", encoding="utf-8")
    db.execute("INSERT INTO courses(course_id,course_name,course_type,owner_id,created_by_role) VALUES('c','course','personal_course','demo_teacher_001','teacher')")
    db.execute("INSERT INTO course_documents(document_id,course_id,uploader_id,original_name,stored_path,mime_type,size_bytes,sha256,status,error_message) VALUES(?,?,?,?,?,?,?,?,?,?)",
               ("d", "c", actor["user_id"], "material.txt", r"F:\old-project\app\data\uploads\course\material.txt", "text/plain", 24, "digest", "failed", "源文件缺失，请重新上传"))
    # INTEGER PRIMARY KEY tables expose rowid under the primary key name unless aliased.
    db.execute("CREATE TABLE portable_artifacts(id INTEGER PRIMARY KEY, stored_path TEXT)")
    db.execute("INSERT INTO portable_artifacts(stored_path) VALUES(?)", (str(upload),))
    db.engine.dispose()
    copied = tmp_path / "new-computer" / "data"
    shutil.copytree(original, copied)
    monkeypatch.setattr(secrets, "KEY_PATH", copied / ".job-secret.key")
    secrets.validate_saved_ai_secrets(copied / "learning.db")
    # The key validation must release its SQLite handle on Windows.
    moved = copied / "moved.db"
    (copied / "learning.db").rename(moved)
    moved.rename(copied / "learning.db")
    migrated = LearningDatabase(copied / "learning.db")
    resolved = AccountAiService(migrated).resolve(actor["user_id"])
    assert resolved["model"] == "chosen-model"
    assert resolved["api_key"] == "test-portable-key"
    doc = migrated.fetch_one("SELECT * FROM course_documents WHERE document_id='d'")
    assert Path(doc["stored_path"]) == copied / "uploads" / "course" / "material.txt"
    assert Path(doc["stored_path"]).read_text() == "already uploaded content"
    assert doc["status"] == "ready"
    assert migrated.fetch_one("SELECT stored_path FROM portable_artifacts")["stored_path"] == doc["stored_path"]
    monkeypatch.setattr(bootstrap_demo, "DB_PATH", copied / "learning.db")
    monkeypatch.setattr(bootstrap_demo, "DATA_DIR", copied)
    monkeypatch.setattr("sys.argv", ["bootstrap_demo.py", "--if-empty"])
    assert not (copied / "demo_credentials.txt").exists()
    assert bootstrap_demo.main() == 0
    assert migrated.fetch_one("SELECT password_hash FROM users WHERE user_id=?", (actor["user_id"],))["password_hash"] == "unchanged"
    assert AccountAiService(migrated).resolve(actor["user_id"])["api_key"] == "test-portable-key"
    migrated.engine.dispose()


def test_bootstrap_does_not_create_uniform_student_password(tmp_path, monkeypatch):
    path = tmp_path / "server.env"
    monkeypatch.setattr(config, "SERVER_ENV", path)
    monkeypatch.setattr(bootstrap_demo, "DB_PATH", tmp_path / "data" / "learning.db")
    monkeypatch.setattr(bootstrap_demo, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr("sys.argv", ["bootstrap_demo.py", "--if-empty"])
    assert bootstrap_demo.main() == 0
    assert not path.exists()
    status = config.student_import_config_status()
    assert status["configured"] is True
    assert status["password_mode"] == "student_number"


def test_export_includes_wal_students_and_keys_without_dependencies(tmp_path):
    import sqlite3
    from zipfile import ZipFile
    from auth_service import AuthService
    from scripts.export_demo import export_demo
    project = tmp_path / "source" / "app"
    project.mkdir(parents=True)
    (project / "start.cmd").write_text("launcher")
    (project.parent / "智教伴学.exe").write_bytes(b"launcher")
    data = tmp_path / "external-data"
    data.mkdir()
    (project / "server.env").write_text(f"ZHIJIAO_DATA_DIR={data}\n")
    original_env = (project / "server.env").read_bytes()
    db = LearningDatabase(data / "learning.db")
    auth = AuthService(db)
    student = auth.create_user("20260001", "unchanged-password-123", "student", "测试学生")
    db.engine.dispose()
    # Hold a WAL connection open so the exporter must capture its committed rows.
    with sqlite3.connect(data / "learning.db") as active:
        active.execute("PRAGMA journal_mode=WAL")
        active.execute("UPDATE users SET display_name='最新测试姓名' WHERE user_id=?", (student["user_id"],))
        active.commit()
        (data / ".job-secret.key").write_bytes(b"portable-key")
        (data / "uploads").mkdir()
        (data / "uploads" / "lesson.txt").write_text("测试资料")
        for folder in (project / ".venv", project / "web" / "node_modules", project / ".runtime"):
            folder.mkdir(parents=True)
            (folder / "excluded.txt").write_text("excluded")
        output = export_demo(project, data, tmp_path / "exports" / "demo.zip")
        with ZipFile(output) as archive:
            names = archive.namelist()
            assert "zhijiao-demo/app/data/.job-secret.key" in names
            assert "zhijiao-demo/智教伴学.exe" in names
            assert not any("excluded.txt" in name or name.endswith(("-wal", "-shm")) for name in names)
            assert "ZHIJIAO_DATA_DIR=data\n" in archive.read("zhijiao-demo/app/server.env").decode()
            archive.extractall(tmp_path / "new-computer")
    copied = LearningDatabase(tmp_path / "new-computer" / "zhijiao-demo" / "app" / "data" / "learning.db")
    copied_auth = AuthService(copied)
    assert copied_auth.login("20260001", "unchanged-password-123")[0]["display_name"] == "最新测试姓名"
    assert copied_auth.secret == auth.secret
    assert (project / "server.env").read_bytes() == original_env
    copied.engine.dispose()
    with pytest.raises(FileExistsError):
        export_demo(project, data, output)


def test_missing_decryption_key_does_not_create_replacement(tmp_path, monkeypatch):
    key_path = tmp_path / "data" / ".job-secret.key"
    monkeypatch.setattr(secrets, "KEY_PATH", key_path)
    encrypted = secrets.encrypt_job_secret("secret")
    key_path.unlink()
    with pytest.raises(RuntimeError, match="job-secret.key"):
        secrets.decrypt_job_secret(encrypted)
    assert not key_path.exists()


def test_custom_data_root_and_json_assets_follow_two_moves(tmp_path):
    import json
    original = tmp_path / "old" / "campus-store"
    original.mkdir(parents=True)
    source = original / "uploads" / "course" / "asset.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"image-content")
    db = LearningDatabase(original / "learning.db")
    db.execute("CREATE TABLE relocation_fixture(id INTEGER PRIMARY KEY, stored_path TEXT, evidence_json TEXT, source_relative_path TEXT)")
    evidence = {"assets": [{"image_path": str(source)}], "chapter_path": ["data", "uploads"], "text": str(source)}
    db.execute("INSERT INTO relocation_fixture VALUES(1,?,?,?)", (str(source), json.dumps(evidence), "uploads/logical-source.png"))
    manifest = source.parent / "manifest.json"
    manifest.write_text(json.dumps({"source_path": str(source)}), encoding="utf-8")
    db.engine.dispose()
    for target in (tmp_path / "new" / "arbitrary-store", tmp_path / "third" / "data"):
        shutil.copytree(original, target)
        migrated = LearningDatabase(target / "learning.db")
        row = migrated.fetch_one("SELECT * FROM relocation_fixture")
        expected = str(target / "uploads" / "course" / "asset.png")
        assert row["stored_path"] == expected
        saved = json.loads(row["evidence_json"])
        assert saved["assets"][0]["image_path"] == expected
        assert saved["chapter_path"] == ["data", "uploads"]
        assert saved["text"] == str(source)
        assert row["source_relative_path"] == "uploads/logical-source.png"
        assert json.loads((target / "uploads" / "course" / "manifest.json").read_text())["source_path"] == expected
        migrated.engine.dispose()
        original = target


def test_relocation_rejects_path_traversal(tmp_path):
    from portable_storage import path_resolver
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("do not rebase")
    resolve, _ = path_resolver(root)
    dangerous = "F:/old/data/../private.txt"
    assert resolve(dangerous) == dangerous


def test_env_settings_follow_copied_folder(tmp_path, monkeypatch):
    for name in config._AI_NAMES:
        monkeypatch.delenv(name, raising=False)
    original = tmp_path / "old-app"
    original.mkdir()
    (original / "user_ai.env").write_text("ZHIJIAO_AI_MODE=custom\nZHIJIAO_CUSTOM_BASE_URL=https://example.com/v1\nZHIJIAO_CUSTOM_MODEL=my-model\nZHIJIAO_CUSTOM_API_KEY=my-test-key\n", encoding="utf-8")
    (original / "server.env").write_text("ZHIJIAO_MINERU_URL=https://parse.example.com/mineru\nZHIJIAO_MINERU_TOKEN=parser-test-token\n", encoding="utf-8")
    copied = tmp_path / "new-app"
    shutil.copytree(original, copied)
    for name, filename in (("USER_AI_ENV", "user_ai.env"), ("SERVER_ENV", "server.env"), ("BUNDLED_RELAY_ENV", "relay_client.env")):
        monkeypatch.setattr(config, name, copied / filename)
    result = config.get_ai_settings(use_request=False)
    assert result["model"] == "my-model"
    assert result["api_key"] == "my-test-key"
    assert config.get_runtime_setting("ZHIJIAO_MINERU_TOKEN") == "parser-test-token"


def test_health_retries_resets_but_does_not_replay_uploaded_post():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import requests
    from parser_transport import create_parser_session

    counts = {"GET": 0, "POST": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            counts["GET"] += 1
            if counts["GET"] < 3:
                self.close_connection = True
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def do_POST(self):
            counts["POST"] += 1
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.close_connection = True

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with create_parser_session(url) as session:
            assert session.get(url, timeout=2).status_code == 200
            with pytest.raises(requests.ConnectionError):
                session.post(url, data=b"upload", timeout=2)
        assert counts == {"GET": 3, "POST": 1}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
