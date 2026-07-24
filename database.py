import json
import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from migrations import apply_migrations


class LearningDatabase:
    """SQLite repository shared by every adapter.

    The legacy learning tables and methods are retained for backwards
    compatibility. New course-scoped data is stored in separate tables so an
    existing MVP database can be upgraded without destructive migrations.
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS questions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT NOT NULL, answer TEXT NOT NULL,
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    knowledge_points_json TEXT NOT NULL DEFAULT '[]',
                    refused INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS practice_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, question_id INTEGER,
                    score REAL NOT NULL, total INTEGER NOT NULL,
                    wrong_items_json TEXT NOT NULL DEFAULT '[]',
                    records_json TEXT NOT NULL DEFAULT '[]',
                    knowledge_points_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS weak_points (
                    knowledge_point TEXT PRIMARY KEY,
                    wrong_count INTEGER NOT NULL DEFAULT 0,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    answered INTEGER NOT NULL DEFAULT 0,
                    correct INTEGER NOT NULL DEFAULT 0,
                    recent_json TEXT NOT NULL DEFAULT '[]',
                    weakness_score REAL NOT NULL DEFAULT 0,
                    level TEXT NOT NULL DEFAULT '数据不足',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS courses (
                    course_id TEXT PRIMARY KEY,
                    course_name TEXT NOT NULL,
                    course_type TEXT NOT NULL CHECK(course_type IN ('shared_course','personal_course')),
                    owner_id TEXT NOT NULL,
                    created_by_role TEXT NOT NULL CHECK(created_by_role IN ('student','teacher','system')),
                    visibility TEXT NOT NULL DEFAULT 'private',
                    description TEXT NOT NULL DEFAULT '',
                    is_virtual INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS course_enrollments (
                    course_id TEXT NOT NULL, student_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(course_id, student_id),
                    FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS course_documents (
                    document_id TEXT PRIMARY KEY, course_id TEXT NOT NULL,
                    uploader_id TEXT NOT NULL, original_name TEXT NOT NULL,
                    stored_path TEXT NOT NULL, mime_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ready', error_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(course_id, sha256),
                    FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS document_chunks (
                    chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL, course_id TEXT NOT NULL,
                    section TEXT NOT NULL DEFAULT '', page_number INTEGER,
                    content TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES course_documents(document_id) ON DELETE CASCADE,
                    FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS course_questions (
                    question_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_id TEXT NOT NULL, user_id TEXT NOT NULL,
                    question TEXT NOT NULL, answer TEXT NOT NULL,
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    knowledge_points_json TEXT NOT NULL DEFAULT '[]',
                    refused INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS course_attempts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_id TEXT NOT NULL, user_id TEXT NOT NULL,
                    question_id INTEGER, score REAL NOT NULL, total INTEGER NOT NULL,
                    wrong_items_json TEXT NOT NULL DEFAULT '[]',
                    records_json TEXT NOT NULL DEFAULT '[]',
                    knowledge_points_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS course_weak_points (
                    course_id TEXT NOT NULL, user_id TEXT NOT NULL, knowledge_point TEXT NOT NULL,
                    answered INTEGER NOT NULL DEFAULT 0, correct INTEGER NOT NULL DEFAULT 0,
                    recent_json TEXT NOT NULL DEFAULT '[]', weakness_score REAL NOT NULL DEFAULT 0,
                    level TEXT NOT NULL DEFAULT '数据不足', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(course_id, user_id, knowledge_point),
                    FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_course ON document_chunks(course_id);
                CREATE INDEX IF NOT EXISTS idx_questions_course ON course_questions(course_id);
                CREATE INDEX IF NOT EXISTS idx_attempts_course ON course_attempts(course_id);

                CREATE TABLE IF NOT EXISTS knowledge_blocks (
                    block_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_id TEXT NOT NULL,
                    document_id TEXT,
                    owner_id TEXT NOT NULL,
                    block_order INTEGER NOT NULL DEFAULT 0,
                    title TEXT NOT NULL,
                    keywords_json TEXT NOT NULL DEFAULT '[]',
                    content TEXT NOT NULL,
                    is_favorite INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE,
                    FOREIGN KEY(document_id) REFERENCES course_documents(document_id) ON DELETE SET NULL
                );
                CREATE TABLE IF NOT EXISTS memory_attempts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_id TEXT NOT NULL,
                    block_id INTEGER,
                    user_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    score REAL NOT NULL,
                    missing_points_json TEXT NOT NULL DEFAULT '[]',
                    error_points_json TEXT NOT NULL DEFAULT '[]',
                    feedback TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE,
                    FOREIGN KEY(block_id) REFERENCES knowledge_blocks(block_id) ON DELETE SET NULL
                );
                CREATE TABLE IF NOT EXISTS generated_practice (
                    practice_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    questions_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS ai_practice_attempts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    questions_json TEXT NOT NULL DEFAULT '[]',
                    responses_json TEXT NOT NULL DEFAULT '[]',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    score REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_blocks_course ON knowledge_blocks(course_id, block_order);
                CREATE INDEX IF NOT EXISTS idx_memory_course ON memory_attempts(course_id, user_id);
                CREATE INDEX IF NOT EXISTS idx_ai_practice_course ON ai_practice_attempts(course_id, user_id);
            """)
            apply_migrations(conn)
            self._backfill_class_scope(conn)

    @staticmethod
    def _backfill_class_scope(conn: sqlite3.Connection) -> None:
        """Give legacy shared courses a deterministic default term and class."""
        courses = conn.execute(
            "SELECT course_id,course_name,owner_id FROM courses WHERE course_type='shared_course'"
        ).fetchall()
        for course in courses:
            owner_key = hashlib.sha256(str(course["owner_id"]).encode()).hexdigest()[:12]
            course_key = hashlib.sha256(str(course["course_id"]).encode()).hexdigest()[:12]
            term_id = f"term_legacy_{owner_key}"
            class_id = f"class_legacy_{course_key}"
            conn.execute(
                "INSERT OR IGNORE INTO terms(term_id,term_name,owner_id) VALUES(?,?,?)",
                (term_id, "默认学期", course["owner_id"]),
            )
            conn.execute(
                """INSERT OR IGNORE INTO classes(class_id,course_id,term_id,class_name,teacher_id)
                   VALUES(?,?,?,?,?)""",
                (class_id, course["course_id"], term_id, f"{course['course_name']}默认班", course["owner_id"]),
            )
            enrollments = conn.execute(
                "SELECT student_id FROM course_enrollments WHERE course_id=?", (course["course_id"],)
            ).fetchall()
            for enrollment in enrollments:
                anon = hashlib.sha256(f"{class_id}:{enrollment['student_id']}".encode()).hexdigest()[:16]
                conn.execute(
                    """INSERT OR IGNORE INTO class_memberships(class_id,student_id,anonymous_id)
                       VALUES(?,?,?)""",
                    (class_id, enrollment["student_id"], anon),
                )

    def execute(self, query: str, params: tuple = ()) -> int:
        with self.connect() as conn:
            cur = conn.execute(query, params)
            return int(cur.lastrowid or 0)

    def fetch_one(self, query: str, params: tuple = ()) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(query, params).fetchone()
            return dict(row) if row else None

    def fetch_all(self, query: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    # Legacy methods kept as a stable public interface.
    def save_question(self, question: str, answer: str, sources: list[dict[str, Any]],
                      knowledge_points: list[str], refused: bool) -> int:
        return self.execute(
            "INSERT INTO questions(question,answer,sources_json,knowledge_points_json,refused) VALUES(?,?,?,?,?)",
            (question, answer, json.dumps(sources, ensure_ascii=False),
             json.dumps(knowledge_points, ensure_ascii=False), int(refused)),
        )

    def save_attempt(self, question_id: int | None, score: float, total: int,
                     wrong_items: list[dict[str, Any]], knowledge_points: list[str],
                     records: list[dict[str, Any]] | None = None) -> int:
        records = records or []
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO practice_attempts(question_id,score,total,wrong_items_json,records_json,knowledge_points_json) VALUES(?,?,?,?,?,?)",
                (question_id, score, total, json.dumps(wrong_items, ensure_ascii=False),
                 json.dumps(records, ensure_ascii=False), json.dumps(knowledge_points, ensure_ascii=False)),
            )
            for record in records:
                for point in record.get("knowledge_points", []):
                    self._update_legacy_point(conn, point, bool(record.get("correct")))
            return int(cur.lastrowid)

    @staticmethod
    def _update_legacy_point(conn: sqlite3.Connection, point: str, is_correct: bool) -> None:
        row = conn.execute("SELECT * FROM weak_points WHERE knowledge_point=?", (point,)).fetchone()
        answered = int(row["answered"] if row else 0) + 1
        correct = int(row["correct"] if row else 0) + int(is_correct)
        recent = (json.loads(row["recent_json"] if row else "[]") + [int(is_correct)])[-10:]
        weakness = round(0.6 * (1 - correct / answered) + 0.4 * (1 - sum(recent) / len(recent)), 4)
        level = "数据不足" if answered < 3 else ("弱" if weakness >= .5 else "中" if weakness >= .25 else "强")
        conn.execute("""
            INSERT INTO weak_points(knowledge_point,wrong_count,attempt_count,answered,correct,recent_json,weakness_score,level)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(knowledge_point) DO UPDATE SET
            wrong_count=excluded.wrong_count, attempt_count=weak_points.attempt_count+1,
            answered=excluded.answered, correct=excluded.correct, recent_json=excluded.recent_json,
            weakness_score=excluded.weakness_score, level=excluded.level, updated_at=CURRENT_TIMESTAMP
        """, (point, answered-correct, 1, answered, correct, json.dumps(recent), weakness, level))

    def update_course_points(self, course_id: str, user_id: str, records: list[dict[str, Any]]) -> None:
        with self.connect() as conn:
            for record in records:
                for point in record.get("knowledge_points", []):
                    row = conn.execute(
                        "SELECT * FROM course_weak_points WHERE course_id=? AND user_id=? AND knowledge_point=?",
                        (course_id, user_id, point),
                    ).fetchone()
                    answered = int(row["answered"] if row else 0) + 1
                    correct = int(row["correct"] if row else 0) + int(bool(record.get("correct")))
                    recent = (json.loads(row["recent_json"] if row else "[]") + [int(bool(record.get("correct")))])[-10:]
                    weakness = round(.6 * (1-correct/answered) + .4 * (1-sum(recent)/len(recent)), 4)
                    level = "数据不足" if answered < 3 else ("弱" if weakness >= .5 else "中" if weakness >= .25 else "强")
                    conn.execute("""
                        INSERT INTO course_weak_points(course_id,user_id,knowledge_point,answered,correct,recent_json,weakness_score,level)
                        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(course_id,user_id,knowledge_point) DO UPDATE SET
                        answered=excluded.answered,correct=excluded.correct,recent_json=excluded.recent_json,
                        weakness_score=excluded.weakness_score,level=excluded.level,updated_at=CURRENT_TIMESTAMP
                    """, (course_id,user_id,point,answered,correct,json.dumps(recent),weakness,level))
