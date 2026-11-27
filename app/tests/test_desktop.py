from fastapi import FastAPI
from fastapi.testclient import TestClient
from desktop_static import VueFiles


def test_history_routes_work_and_missing_api_assets_remain_404(tmp_path):
    (tmp_path / "index.html").write_text('<div id="app"></div>', encoding="utf-8")
    app = FastAPI()
    app.mount("/", VueFiles(directory=tmp_path, html=True))
    with TestClient(app) as client:
        for route in ("/", "/login", "/teaching-archive", "/student/courses"):
            assert client.get(route).text == '<div id="app"></div>'
        for route in ("/api", "/api/v1/missing", "/assets/missing", "/models/missing", "/favicon.ico"):
            assert client.get(route).status_code == 404


def test_desktop_health_does_not_match_another_installation(monkeypatch):
    import io
    import json
    from scripts import run_desktop
    monkeypatch.setattr(run_desktop, "source_fingerprint", lambda: "new-code")
    monkeypatch.setattr(run_desktop, "runtime_instance_id", lambda: "this-installation")
    class Opener:
        def open(self, *args, **kwargs):
            return io.BytesIO(json.dumps({"runtime_source_fingerprint": "new-code",
                "runtime_instance_id": "other-installation", "desktop_ui": True}).encode())
    monkeypatch.setattr(run_desktop.urllib.request, "build_opener", lambda *args: Opener())
    assert not run_desktop.matches(5173)
