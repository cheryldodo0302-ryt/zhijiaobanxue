import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class LearningDatabase:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
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
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    knowledge_points_json TEXT NOT NULL DEFAULT '[]',
                    refused INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS practice_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question_id INTEGER,
                    score REAL NOT NULL,
                    total INTEGER NOT NULL,
                    wrong_items_json TEXT NOT NULL DEFAULT '[]',
                    records_json TEXT NOT NULL DEFAULT '[]',
                    knowledge_points_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(question_id) REFERENCES questions(id)
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
            """)
            self._ensure_columns(conn, "practice_attempts", {"records_json": "TEXT NOT NULL DEFAULT '[]'"})
            self._ensure_columns(conn, "weak_points", {
                "answered": "INTEGER NOT NULL DEFAULT 0",
                "correct": "INTEGER NOT NULL DEFAULT 0",
                "recent_json": "TEXT NOT NULL DEFAULT '[]'",
                "weakness_score": "REAL NOT NULL DEFAULT 0",
                "level": "TEXT NOT NULL DEFAULT '数据不足'",
            })

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def save_question(self, question: str, answer: str, sources: list[dict[str, Any]],
                      knowledge_points: list[str], refused: bool) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO questions(question,answer,sources_json,knowledge_points_json,refused) VALUES(?,?,?,?,?)",
                (question, answer, json.dumps(sources, ensure_ascii=False),
                 json.dumps(knowledge_points, ensure_ascii=False), int(refused)),
            )
            return int(cur.lastrowid)

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
            if records:
                for record in records:
                    for point in record.get("knowledge_points", []):
                        self._update_point(conn, point, bool(record.get("correct")))
            else:
                wrong_points = {p for item in wrong_items for p in item.get("knowledge_points", [])}
                for point in set(knowledge_points):
                    self._update_point(conn, point, point not in wrong_points)
            return int(cur.lastrowid)

    @staticmethod
    def _update_point(conn: sqlite3.Connection, point: str, is_correct: bool) -> None:
        row = conn.execute("SELECT * FROM weak_points WHERE knowledge_point=?", (point,)).fetchone()
        answered = int(row["answered"] if row else 0) + 1
        correct = int(row["correct"] if row else 0) + int(is_correct)
        recent = json.loads(row["recent_json"] if row else "[]")
        recent = (recent + [int(is_correct)])[-10:]
        accuracy = correct / answered
        recent_accuracy = sum(recent) / len(recent)
        weakness = round(0.6 * (1 - accuracy) + 0.4 * (1 - recent_accuracy), 4)
        level = "数据不足" if answered < 3 else ("弱" if weakness >= 0.5 else "中" if weakness >= 0.25 else "强")
        wrong_count = answered - correct
        conn.execute("""
            INSERT INTO weak_points(knowledge_point,wrong_count,attempt_count,answered,correct,recent_json,weakness_score,level)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(knowledge_point) DO UPDATE SET
              wrong_count=excluded.wrong_count,
              attempt_count=weak_points.attempt_count+1,
              answered=excluded.answered,
              correct=excluded.correct,
              recent_json=excluded.recent_json,
              weakness_score=excluded.weakness_score,
              level=excluded.level,
              updated_at=CURRENT_TIMESTAMP
        """, (point, wrong_count, 1, answered, correct, json.dumps(recent), weakness, level))

    def fetch_all(self, query: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]
