from __future__ import annotations

import csv
import hashlib
import io
import json
import mimetypes
import re
import shutil
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from config import DATA_DIR, MAX_EVIDENCE_CHARS, MIN_EVIDENCE_SCORE, TOP_K
from database import LearningDatabase
from llm_provider import LLMProvider, build_backend_provider
from skills.exercise import ExerciseItem, generate_exercises, grade_exercises
from skills.qa import answer_question
from skills.retrieval import Evidence


class CampusError(Exception):
    pass


class PermissionDenied(CampusError):
    pass


class ValidationError(CampusError):
    pass


class NotFound(CampusError):
    pass


ALLOWED_FILES = {
    ".pdf": {"application/pdf"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/octet-stream"},
    ".pptx": {"application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/octet-stream"},
    ".md": {"text/markdown", "text/plain", "application/octet-stream"},
    ".txt": {"text/plain", "application/octet-stream"},
}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(value: str | None) -> Any:
    return json.loads(value or "[]")


def _safe_name(name: str) -> str:
    name = Path(name).name.strip().replace("\x00", "")
    stem = re.sub(r"[^\w\u4e00-\u9fff .()-]", "_", Path(name).stem)[:80].strip(" .") or "document"
    suffix = Path(name).suffix.lower()
    return f"{stem}{suffix}"


def _split_text(text: str, default_section: str = "正文", page_number: int | None = None) -> list[dict]:
    chunks: list[dict] = []
    section = default_section
    buffer: list[str] = []

    def flush() -> None:
        content = "\n".join(buffer).strip()
        if not content:
            return
        for start in range(0, len(content), 900):
            part = content[start:start + 1100].strip()
            if part:
                chunks.append({"section": section, "page_number": page_number, "content": part})

    for line in text.splitlines():
        if re.match(r"^#{1,6}\s+", line):
            flush()
            buffer.clear()
            section = re.sub(r"^#{1,6}\s+", "", line).strip() or default_section
        elif line.strip():
            buffer.append(line.strip())
    flush()
    return chunks


def parse_document(data: bytes, suffix: str) -> list[dict]:
    if suffix in {".md", ".txt"}:
        try:
            return _split_text(data.decode("utf-8-sig"))
        except UnicodeDecodeError as exc:
            raise ValidationError("文本文件必须使用 UTF-8 编码") from exc
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        chunks: list[dict] = []
        for index, page in enumerate(reader.pages, 1):
            chunks += _split_text(page.extract_text() or "", f"第 {index} 页", index)
        return chunks
    if suffix == ".docx":
        from docx import Document
        doc = Document(io.BytesIO(data))
        lines = []
        for paragraph in doc.paragraphs:
            text = paragraph.text.strip()
            if text:
                lines.append(("# " if paragraph.style.name.startswith("Heading") else "") + text)
        return _split_text("\n".join(lines))
    if suffix == ".pptx":
        from pptx import Presentation
        prs = Presentation(io.BytesIO(data))
        chunks = []
        for index, slide in enumerate(prs.slides, 1):
            texts = [shape.text.strip() for shape in slide.shapes if hasattr(shape, "text") and shape.text.strip()]
            if texts:
                chunks += _split_text("\n".join(texts), f"第 {index} 页", index)
        return chunks
    raise ValidationError("不支持的文件类型")


class ChunkRetriever:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.chunks = rows
        self.vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, 3), min_df=1)
        self.matrix = self.vectorizer.fit_transform([row["content"] for row in rows]) if rows else None

    @staticmethod
    def _keywords(text: str) -> set[str]:
        runs = re.findall(r"[\u4e00-\u9fff]+", text)
        chinese = {run[i:i+n] for run in runs for n in (2, 3, 4) for i in range(max(len(run)-n+1, 0))}
        return chinese | set(re.findall(r"[A-Za-z0-9_-]{2,}", text.lower()))

    def search(self, query: str, top_k: int = 4) -> list[Evidence]:
        if not query.strip() or self.matrix is None:
            return []
        scores = cosine_similarity(self.vectorizer.transform([query]), self.matrix)[0]
        query_keys = self._keywords(query)
        ranked = []
        for index, row in enumerate(self.rows):
            keys = self._keywords(row["content"] + " " + row["section"])
            score = .72 * float(scores[index]) + .28 * len(query_keys & keys) / max(len(query_keys), 1)
            ranked.append((score, row))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [Evidence(row["original_name"], row["section"] or "正文", row["content"], round(score, 4))
                for score, row in ranked[:top_k] if score > 0]


class CampusService:
    def __init__(self, db: LearningDatabase, storage_dir: Path | str | None = None,
                 provider_factory=None):
        self.db = db
        self.storage_dir = Path(storage_dir or DATA_DIR / "uploads")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.provider_factory = provider_factory or build_backend_provider

    def get_course(self, course_id: str) -> dict:
        course = self.db.fetch_one("SELECT * FROM courses WHERE course_id=?", (course_id,))
        if not course:
            raise NotFound("课程不存在")
        return course

    def _can_access(self, course: dict, user_id: str, role: str) -> bool:
        if course["course_type"] == "personal_course":
            return role == "student" and course["owner_id"] == user_id
        if role == "teacher":
            return course["owner_id"] == user_id
        if role == "student":
            if course["visibility"] == "public":
                return True
            return self.db.fetch_one("SELECT 1 ok FROM course_enrollments WHERE course_id=? AND student_id=?",
                                     (course["course_id"], user_id)) is not None
        return False

    def require_access(self, course_id: str, user_id: str, role: str) -> dict:
        course = self.get_course(course_id)
        if not self._can_access(course, user_id, role):
            raise PermissionDenied("无权访问该课程")
        return course

    def list_courses(self, user_id: str, role: str) -> list[dict]:
        rows = self.db.fetch_all("SELECT * FROM courses ORDER BY updated_at DESC")
        return [row for row in rows if self._can_access(row, user_id, role)]

    def create_course(self, name: str, course_type: str, user_id: str, role: str,
                      description: str = "", visibility: str | None = None) -> dict:
        name = name.strip()
        if not name:
            raise ValidationError("课程名称不能为空")
        expected = "personal_course" if role == "student" else "shared_course" if role == "teacher" else ""
        if course_type != expected:
            raise PermissionDenied("学生只能创建个人课程，教师只能创建共享课程")
        course_id = f"{'pc' if course_type == 'personal_course' else 'sc'}_{uuid.uuid4().hex[:12]}"
        self.db.execute("""INSERT INTO courses(course_id,course_name,course_type,owner_id,created_by_role,visibility,description)
                         VALUES(?,?,?,?,?,?,?)""",
                        (course_id, name, course_type, user_id, role,
                         visibility or ("private" if course_type == "personal_course" else "enrolled"), description.strip()))
        return self.get_course(course_id)

    def update_course(self, course_id: str, user_id: str, role: str, **changes: Any) -> dict:
        course = self.require_access(course_id, user_id, role)
        if course["owner_id"] != user_id:
            raise PermissionDenied("只有课程所有者可以修改课程")
        name = str(changes.get("course_name", course["course_name"])).strip()
        description = str(changes.get("description", course["description"])).strip()
        visibility = str(changes.get("visibility", course["visibility"]))
        if course["course_type"] == "personal_course":
            visibility = "private"
        if visibility not in {"private", "enrolled", "public"}:
            raise ValidationError("课程可见性不合法")
        self.db.execute("UPDATE courses SET course_name=?,description=?,visibility=?,updated_at=CURRENT_TIMESTAMP WHERE course_id=?",
                        (name, description, visibility, course_id))
        return self.get_course(course_id)

    def delete_personal_course(self, course_id: str, user_id: str) -> None:
        course = self.require_access(course_id, user_id, "student")
        if course["course_type"] != "personal_course" or course["owner_id"] != user_id:
            raise PermissionDenied("只能删除自己创建的个人课程")
        upload_root = self.storage_dir.resolve()
        course_dir = (upload_root / course_id).resolve()
        if upload_root not in course_dir.parents:
            raise ValidationError("课程存储路径不安全")
        self.db.execute("DELETE FROM courses WHERE course_id=?", (course_id,))
        if course_dir.exists():
            shutil.rmtree(course_dir)

    def upsert_virtual_course(self, course_id: str, name: str, teacher_id: str,
                              description: str = "", visibility: str = "public") -> dict:
        """Stable insertion/update hook for demo or externally provisioned courses."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{3,64}", course_id):
            raise ValidationError("虚拟课程 ID 只能包含字母、数字、下划线和连字符")
        existing = self.db.fetch_one("SELECT owner_id FROM courses WHERE course_id=?", (course_id,))
        if existing and existing["owner_id"] != teacher_id:
            raise PermissionDenied("不能修改其他教师拥有的虚拟课程")
        self.db.execute("""INSERT INTO courses(course_id,course_name,course_type,owner_id,created_by_role,visibility,description,is_virtual)
                         VALUES(?,?,'shared_course',?,'teacher',?,?,1)
                         ON CONFLICT(course_id) DO UPDATE SET course_name=excluded.course_name,
                         owner_id=excluded.owner_id,visibility=excluded.visibility,description=excluded.description,
                         is_virtual=1,updated_at=CURRENT_TIMESTAMP""",
                        (course_id, name.strip(), teacher_id, visibility, description.strip()))
        return self.get_course(course_id)

    def enroll_student(self, course_id: str, teacher_id: str, student_id: str) -> None:
        course = self.require_access(course_id, teacher_id, "teacher")
        if course["course_type"] != "shared_course" or course["owner_id"] != teacher_id:
            raise PermissionDenied("只能为自己创建的共享课程授权")
        if not student_id.strip():
            raise ValidationError("学生 ID 不能为空")
        self.db.execute("INSERT OR IGNORE INTO course_enrollments(course_id,student_id) VALUES(?,?)",
                        (course_id, student_id.strip()))

    def list_enrollments(self, course_id: str, teacher_id: str) -> list[dict]:
        course = self.require_access(course_id, teacher_id, "teacher")
        if course["owner_id"] != teacher_id:
            raise PermissionDenied("无权查看授权名单")
        return self.db.fetch_all("SELECT student_id,created_at FROM course_enrollments WHERE course_id=?", (course_id,))

    def upload_document(self, course_id: str, user_id: str, role: str, name: str,
                        mime_type: str, data: bytes) -> dict:
        course = self.require_access(course_id, user_id, role)
        if role == "student" and (course["course_type"] != "personal_course" or course["owner_id"] != user_id):
            raise PermissionDenied("学生只能向自己的个人课程上传资料")
        if role == "teacher" and (course["course_type"] != "shared_course" or course["owner_id"] != user_id):
            raise PermissionDenied("教师只能向自己的共享课程上传资料")
        safe_name = _safe_name(name)
        suffix = Path(safe_name).suffix.lower()
        if suffix not in ALLOWED_FILES or mime_type not in ALLOWED_FILES[suffix]:
            raise ValidationError("扩展名与 MIME 类型不匹配或不受支持")
        if not data:
            raise ValidationError("不能上传空文件")
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValidationError("文件不能超过 20MB")
        digest = hashlib.sha256(data).hexdigest()
        if self.db.fetch_one("SELECT 1 ok FROM course_documents WHERE course_id=? AND sha256=?", (course_id, digest)):
            raise ValidationError("该课程中已存在内容相同的文件")
        document_id = f"doc_{uuid.uuid4().hex}"
        destination = (self.storage_dir / course_id / f"{document_id}_{safe_name}").resolve()
        root = (self.storage_dir / course_id).resolve()
        if root not in destination.parents:
            raise ValidationError("文件路径不安全")
        chunks = parse_document(data, suffix)
        if not chunks:
            raise ValidationError("文件未解析出有效文字")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        with self.db.connect() as conn:
            conn.execute("""INSERT INTO course_documents(document_id,course_id,uploader_id,original_name,stored_path,mime_type,size_bytes,sha256,status)
                          VALUES(?,?,?,?,?,?,?,?,?)""",
                         (document_id, course_id, user_id, safe_name, str(destination), mime_type, len(data), digest, "ready"))
            conn.executemany("INSERT INTO document_chunks(document_id,course_id,section,page_number,content) VALUES(?,?,?,?,?)",
                             [(document_id, course_id, x["section"], x["page_number"], x["content"]) for x in chunks])
        return {"document_id": document_id, "file_name": safe_name, "status": "ready", "chunk_count": len(chunks)}

    def list_documents(self, course_id: str, user_id: str, role: str) -> list[dict]:
        self.require_access(course_id, user_id, role)
        return self.db.fetch_all("""SELECT d.document_id,d.original_name,d.mime_type,d.size_bytes,d.status,d.error_message,d.created_at,
                                   COUNT(c.chunk_id) chunk_count,
                                   (SELECT dc.content FROM document_chunks dc WHERE dc.document_id=d.document_id ORDER BY dc.chunk_id LIMIT 1) text_preview
                                   FROM course_documents d LEFT JOIN document_chunks c ON c.document_id=d.document_id
                                   WHERE d.course_id=? GROUP BY d.document_id ORDER BY d.created_at DESC""", (course_id,))

    def delete_document(self, document_id: str, user_id: str, role: str) -> None:
        doc = self.db.fetch_one("SELECT d.*,c.course_type,c.owner_id FROM course_documents d JOIN courses c USING(course_id) WHERE document_id=?",
                                (document_id,))
        if not doc:
            raise NotFound("资料不存在")
        if doc["owner_id"] != user_id or (role == "student" and doc["course_type"] != "personal_course") or (role == "teacher" and doc["course_type"] != "shared_course"):
            raise PermissionDenied("无权删除该资料")
        self.db.execute("DELETE FROM course_documents WHERE document_id=?", (document_id,))
        path = Path(doc["stored_path"])
        if path.exists() and self.storage_dir.resolve() in path.resolve().parents:
            path.unlink()

    def knowledge_status(self, course_id: str, user_id: str, role: str) -> dict:
        self.require_access(course_id, user_id, role)
        row = self.db.fetch_one("""SELECT COUNT(DISTINCT d.document_id) document_count,COUNT(c.chunk_id) chunk_count,
                                  COALESCE(SUM(LENGTH(c.content)),0) character_count
                                  FROM course_documents d LEFT JOIN document_chunks c ON c.document_id=d.document_id WHERE d.course_id=?""", (course_id,))
        return {**(row or {}), "status": "ready" if row and row["chunk_count"] else "empty"}

    def _retriever(self, course_id: str) -> ChunkRetriever:
        rows = self.db.fetch_all("""SELECT c.content,c.section,c.page_number,d.original_name
                                  FROM document_chunks c JOIN course_documents d USING(document_id)
                                  WHERE c.course_id=? AND d.status='ready'""", (course_id,))
        return ChunkRetriever(rows)

    def ask(self, course_id: str, user_id: str, role: str, question: str,
            provider: LLMProvider | None = None) -> dict:
        self.require_access(course_id, user_id, role)
        if not question.strip():
            raise ValidationError("问题不能为空")
        result = answer_question(question.strip(), self._retriever(course_id), provider or self.provider_factory(),
                                 MIN_EVIDENCE_SCORE, TOP_K)
        qid = self.db.execute("""INSERT INTO course_questions(course_id,user_id,question,answer,sources_json,knowledge_points_json,refused)
                               VALUES(?,?,?,?,?,?,?)""",
                              (course_id,user_id,question.strip(),result.answer,_json([e.to_dict() for e in result.evidence]),
                               _json(result.knowledge_points),int(result.refused)))
        return {"question_id": qid, "answer": result.answer, "sources": [e.to_dict() for e in result.evidence],
                "knowledge_points": result.knowledge_points, "refused": result.refused}

    def generate_quiz(self, course_id: str, user_id: str, role: str, question_id: int | None = None) -> dict:
        self.require_access(course_id, user_id, role)
        params: tuple = (course_id, user_id)
        sql = "SELECT * FROM course_questions WHERE course_id=? AND user_id=? AND refused=0"
        if question_id is not None:
            sql += " AND question_id=?"
            params += (question_id,)
        row = self.db.fetch_one(sql + " ORDER BY question_id DESC LIMIT 1", params)
        if not row:
            raise ValidationError("请先完成一次有资料证据支持的课程问答")
        weak = self.profile(course_id, user_id, role)["weak_points"]
        items = generate_exercises(row["answer"], _loads(row["knowledge_points_json"]), weak)
        return {"question_id": row["question_id"], "items": [item.to_dict() for item in items]}

    def submit_quiz(self, course_id: str, user_id: str, role: str, question_id: int | None,
                    items: list[dict], responses: list[str | None]) -> dict:
        self.require_access(course_id, user_id, role)
        exercise_items = [ExerciseItem(**item) for item in items]
        grade = grade_exercises(exercise_items, responses)
        points = sorted({point for item in exercise_items for point in item.knowledge_points})
        attempt_id = self.db.execute("""INSERT INTO course_attempts(course_id,user_id,question_id,score,total,wrong_items_json,records_json,knowledge_points_json)
                                    VALUES(?,?,?,?,?,?,?,?)""",
                                   (course_id,user_id,question_id,grade.score,grade.total,_json(grade.wrong_items),
                                    _json(grade.records),_json(points)))
        self.db.update_course_points(course_id, user_id, grade.records)
        return {"attempt_id": attempt_id, "score": grade.score, "correct_count": grade.correct_count,
                "total": grade.total, "records": grade.records, "wrong_items": grade.wrong_items,
                "topic_stats": grade.topic_stats}

    def profile(self, course_id: str, user_id: str, role: str) -> dict:
        self.require_access(course_id, user_id, role)
        if role != "student":
            raise PermissionDenied("学习画像仅供学生本人查看")
        questions = self.db.fetch_all("SELECT * FROM course_questions WHERE course_id=? AND user_id=? ORDER BY question_id DESC LIMIT 20", (course_id,user_id))
        attempts = self.db.fetch_all("SELECT * FROM course_attempts WHERE course_id=? AND user_id=? ORDER BY attempt_id DESC LIMIT 20", (course_id,user_id))
        weak = self.db.fetch_all("SELECT * FROM course_weak_points WHERE course_id=? AND user_id=? ORDER BY weakness_score DESC,answered DESC", (course_id,user_id))
        for row in questions:
            row["sources"] = _loads(row.pop("sources_json")); row["knowledge_points"] = _loads(row.pop("knowledge_points_json"))
        for row in attempts:
            row["wrong_items"] = _loads(row.pop("wrong_items_json")); row["records"] = _loads(row.pop("records_json")); row["knowledge_points"] = _loads(row.pop("knowledge_points_json"))
        return {"questions": questions, "attempts": attempts, "weak_points": weak,
                "wrong_questions": [x for a in attempts for x in a["wrong_items"]]}

    def class_analysis(self, course_id: str, teacher_id: str) -> dict:
        course = self.require_access(course_id, teacher_id, "teacher")
        if course["course_type"] != "shared_course" or course["owner_id"] != teacher_id:
            raise PermissionDenied("教师只能分析自己的共享课程")
        questions = self.db.fetch_all("SELECT question,refused,knowledge_points_json FROM course_questions WHERE course_id=?", (course_id,))
        attempts = self.db.fetch_all("SELECT score,total,records_json FROM course_attempts WHERE course_id=?", (course_id,))
        frequent = [{"question": q, "count": n} for q,n in Counter(x["question"] for x in questions).most_common(10)]
        uncovered = [{"question": q, "count": n} for q,n in Counter(x["question"] for x in questions if x["refused"]).most_common(10)]
        point_stats: dict[str, dict[str,int]] = defaultdict(lambda: {"answered":0,"correct":0})
        for attempt in attempts:
            for record in _loads(attempt["records_json"]):
                for point in record.get("knowledge_points", []):
                    point_stats[point]["answered"] += 1
                    point_stats[point]["correct"] += int(bool(record.get("correct")))
        weak = [{"knowledge_point": p, **s, "accuracy": round(100*s["correct"]/s["answered"],1) if s["answered"] else 0}
                for p,s in point_stats.items()]
        weak.sort(key=lambda x: (x["accuracy"], -x["answered"]))
        avg = round(sum(x["score"] for x in attempts)/len(attempts),1) if attempts else 0
        return {"question_count": len(questions), "quiz_count": len(attempts), "average_score": avg,
                "frequent_questions": frequent, "uncovered_questions": uncovered, "weak_points": weak,
                "privacy": "仅展示共享课程的匿名聚合结果"}

    def teaching_report(self, course_id: str, teacher_id: str) -> dict:
        course = self.get_course(course_id)
        analysis = self.class_analysis(course_id, teacher_id)
        phenomena, evidence, suggestions = [], [], []
        if analysis["weak_points"]:
            point = analysis["weak_points"][0]
            phenomena.append(f"学生在“{point['knowledge_point']}”上的掌握较弱")
            evidence.append(f"该知识点共作答 {point['answered']} 次，正确率 {point['accuracy']}%")
            suggestions.append(f"围绕“{point['knowledge_point']}”增加例题讲解与分层练习")
        if analysis["uncovered_questions"]:
            phenomena.append("部分学生问题未被现有资料充分覆盖")
            evidence.append(f"共有 {sum(x['count'] for x in analysis['uncovered_questions'])} 次问题因资料不足未能回答")
            suggestions.append("补充对应章节资料，并在入库后重新检查检索覆盖率")
        if not phenomena:
            phenomena.append("当前共享课程尚缺少足够学习行为数据")
            evidence.append(f"已记录 {analysis['question_count']} 次提问和 {analysis['quiz_count']} 次练习")
            suggestions.append("先组织一次课程问答与形成性练习，再依据真实数据调整教学")
        return {"course_id": course_id, "course_name": course["course_name"], "phenomena": phenomena,
                "evidence": evidence, "suggestions": suggestions, "analysis": analysis}

    def export_student_csv(self, course_id: str, user_id: str) -> bytes:
        profile = self.profile(course_id, user_id, "student")
        output = io.StringIO(); writer = csv.writer(output)
        writer.writerow(["类型","时间","内容","成绩/状态"])
        for row in profile["questions"]:
            writer.writerow(["提问",row["created_at"],row["question"],"资料不足" if row["refused"] else "已回答"])
        for row in profile["attempts"]:
            writer.writerow(["练习",row["created_at"],f"错题 {len(row['wrong_items'])} 道",row["score"]])
        return ("\ufeff"+output.getvalue()).encode("utf-8")

    def export_class_csv(self, course_id: str, teacher_id: str) -> bytes:
        report = self.teaching_report(course_id, teacher_id)
        output = io.StringIO(); writer = csv.writer(output)
        writer.writerow(["现象","证据","建议"])
        for i in range(max(len(report["phenomena"]),len(report["evidence"]),len(report["suggestions"]))):
            writer.writerow([report["phenomena"][i] if i < len(report["phenomena"]) else "",
                             report["evidence"][i] if i < len(report["evidence"]) else "",
                             report["suggestions"][i] if i < len(report["suggestions"]) else ""])
        return ("\ufeff"+output.getvalue()).encode("utf-8")

    def export_class_excel(self, course_id: str, teacher_id: str) -> bytes:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        report = self.teaching_report(course_id, teacher_id)
        workbook = Workbook(); sheet = workbook.active; sheet.title = "教学改进"
        sheet.append(["现象", "证据", "建议"])
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF"); cell.fill = PatternFill("solid", fgColor="D98686")
        for i in range(max(len(report["phenomena"]), len(report["evidence"]), len(report["suggestions"]))):
            sheet.append([report["phenomena"][i] if i < len(report["phenomena"]) else "",
                          report["evidence"][i] if i < len(report["evidence"]) else "",
                          report["suggestions"][i] if i < len(report["suggestions"]) else ""])
        sheet.column_dimensions["A"].width = 34; sheet.column_dimensions["B"].width = 42; sheet.column_dimensions["C"].width = 42
        analysis = workbook.create_sheet("匿名聚合")
        analysis.append(["指标", "数值"])
        analysis.append(["提问次数", report["analysis"]["question_count"]])
        analysis.append(["练习次数", report["analysis"]["quiz_count"]])
        analysis.append(["平均分", report["analysis"]["average_score"]])
        output = io.BytesIO(); workbook.save(output); return output.getvalue()

    def export_class_word(self, course_id: str, teacher_id: str) -> bytes:
        from docx import Document
        from docx.shared import Pt
        report = self.teaching_report(course_id, teacher_id)
        document = Document(); document.add_heading(f"{report['course_name']} 学情报告", 0)
        document.add_paragraph("本报告仅使用共享课程中的匿名聚合数据。")
        for index, phenomenon in enumerate(report["phenomena"]):
            document.add_heading(f"改进建议 {index + 1}", level=1)
            document.add_paragraph(f"现象：{phenomenon}")
            document.add_paragraph(f"证据：{report['evidence'][min(index, len(report['evidence'])-1)]}")
            document.add_paragraph(f"建议：{report['suggestions'][min(index, len(report['suggestions'])-1)]}")
        style = document.styles["Normal"]; style.font.name = "Microsoft YaHei"; style.font.size = Pt(10.5)
        output = io.BytesIO(); document.save(output); return output.getvalue()

    def seed_demo(self, materials_dir: Path) -> None:
        self.upsert_virtual_course("virtual_ai_101", "人工智能基础（虚拟课程）", "demo_teacher_001",
                                   "用于演示教师共享课程、问答、练习和匿名学情分析。", "public")
        existing = self.db.fetch_one("SELECT COUNT(*) n FROM course_documents WHERE course_id='virtual_ai_101'")
        if existing and existing["n"]:
            return
        for path in sorted(materials_dir.glob("*.md")):
            data = path.read_bytes()
            self.upload_document("virtual_ai_101", "demo_teacher_001", "teacher", path.name, "text/markdown", data)
