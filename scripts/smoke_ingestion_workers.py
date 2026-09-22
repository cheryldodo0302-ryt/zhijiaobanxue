from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auth_service import AuthService
from campus_service import CampusService
from database import LearningDatabase
from ingestion_service import IngestionService
from teacher_service import TeacherService


def main() -> None:
    os.environ.setdefault("ZHIJIAO_MINERU_URL", "http://127.0.0.1:18000")
    os.environ.setdefault("ZHIJIAO_MINERU_BACKEND", "pipeline")
    os.environ.setdefault("ZHIJIAO_FORMULA_URL", "http://127.0.0.1:18100")
    sample = Path(tempfile.gettempdir()) / "zhijiao-ingestion-smoke" / "scanned_formula.pdf"
    if not sample.exists():
        raise SystemExit("Run scripts/create_ingestion_smoke_fixture.py first")
    root = Path(tempfile.mkdtemp(prefix="zhijiao-worker-e2e-"))
    db = LearningDatabase(root / "smoke.db")
    campus = CampusService(db, root / "uploads", provider_factory=lambda: None)
    teacher = AuthService(db, root / "secret").create_user("smoke-teacher", "smoke-password-123", "teacher")
    course = TeacherService(db, campus).create_course(teacher, "Worker smoke test")
    ingestion = IngestionService(db, campus)
    job = ingestion.queue_document(
        teacher, course["course_id"], sample.name, "application/pdf", sample.read_bytes()
    )
    ingestion.process_job(job["job_id"])
    completed = ingestion.get_job(teacher, job["job_id"])
    blocks = ingestion.list_blocks(teacher, completed["document_id"])
    formulas = []
    for block in blocks:
        if block["block_type"] != "formula":
            continue
        raw = json.loads(block["raw_payload_json"])
        formulas.append({
            "mineru": block["latex"],
            "pix2text": raw.get("formula_secondary_latex"),
            "consistent": raw.get("formula_consistent"),
            "status": block["verification_status"],
            "source_image_exists": Path(block["source_image_path"]).exists(),
        })
    print(json.dumps({"job": completed["status"], "blocks": len(blocks), "formulas": formulas}, ensure_ascii=True))


if __name__ == "__main__":
    main()
