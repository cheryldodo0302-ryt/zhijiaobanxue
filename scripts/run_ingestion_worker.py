from __future__ import annotations

import time
import sys
import socket
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from campus_service import CampusService
from config import DB_PATH
from database import LearningDatabase
from ingestion_service import IngestionService
from runtime_contract import RUNTIME_SOURCE_FINGERPRINT


def acquire_single_worker_lock():
    # A localhost socket avoids filesystem ACL/encoding differences on
    # Windows while still being released automatically when the worker exits.
    handle = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        handle.bind(("127.0.0.1", 17654))
        handle.listen(1)
    except OSError:
        handle.close()
        raise RuntimeError(
            "已有知识解析 Worker 正在运行，请勿重复启动系统。"
        )
    return handle


def serve_runtime_contract(handle: socket.socket) -> None:
    """Expose the loaded source fingerprint on the worker lock socket."""
    while True:
        try:
            connection, _address = handle.accept()
            with connection:
                connection.sendall(RUNTIME_SOURCE_FINGERPRINT.encode("ascii"))
        except OSError:
            return


def main() -> None:
    worker_lock = acquire_single_worker_lock()
    threading.Thread(
        target=serve_runtime_contract, args=(worker_lock,), daemon=True
    ).start()
    db = LearningDatabase(DB_PATH)
    service = IngestionService(db, CampusService(db))
    db.execute("UPDATE ingestion_jobs SET status='queued' WHERE status='running'")
    db.execute(
        """UPDATE semantic_analysis_jobs
           SET status='queued',current_stage='resuming_after_restart',updated_at=CURRENT_TIMESTAMP
           WHERE status IN ('running','retry_wait')"""
    )
    print("Knowledge ingestion worker started. Press Ctrl+C to stop.")
    while True:
        row = db.fetch_one("SELECT job_id FROM ingestion_jobs WHERE status='queued' ORDER BY created_at LIMIT 1")
        if row:
            service.process_job(row["job_id"])
            continue
        semantic = db.fetch_one(
            """SELECT analysis_job_id FROM semantic_analysis_jobs
               WHERE status='queued'
                  OR (status='retry_wait' AND COALESCE(next_retry_at,updated_at)<=CURRENT_TIMESTAMP)
               ORDER BY CASE status WHEN 'queued' THEN 0 ELSE 1 END,created_at LIMIT 1"""
        )
        if semantic:
            service.process_semantic_analysis(semantic["analysis_job_id"])
            continue
        time.sleep(2)


if __name__ == "__main__":
    main()
