"""Formal class assessments. Frozen questions and rosters never use private practice."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from uuid import uuid4

from campus_service import PermissionDenied, ValidationError, NotFound
from question_bank_service import QuestionBankService


def utc_now():
    return datetime.now(timezone.utc)


def timestamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError) as exc:
        raise ValidationError("时间必须是带时区的 ISO 日期时间") from exc


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ClassTaskService:
    def __init__(self, campus, clock=utc_now):
        self.campus, self.db, self.clock = campus, campus.db, clock

    def scope(self, actor, course_id, class_id, student_id=None):
        role, uid = actor.get("role"), actor.get("user_id")
        if role not in {"student", "teacher"} or not uid:
            raise PermissionDenied("用户角色不合法")
        course = self.campus.require_access(course_id, uid, role)
        row = self.db.fetch_one("SELECT * FROM classes WHERE class_id=? AND course_id=? AND status='active'", (class_id, course_id))
        if course["course_type"] != "shared_course" or not row:
            raise PermissionDenied("仅支持当前共享课程的有效教学班")
        if role == "teacher" and (row["teacher_id"] != uid or course["owner_id"] != uid):
            raise PermissionDenied("无权查看该教学班")
        target = uid if role == "student" else student_id
        if role == "student" and student_id and student_id != uid:
            raise PermissionDenied("只能访问自己的学习记录")
        if target and not self.db.fetch_one("""SELECT 1 FROM class_memberships m JOIN users u ON u.user_id=m.student_id
            WHERE m.class_id=? AND m.student_id=? AND m.status='active' AND u.status='active' AND u.role='student'""", (class_id, target)):
            raise PermissionDenied("学生不属于当前教学班")
        return row

    def student_scopes(self, actor):
        if actor.get("role") != "student":
            raise PermissionDenied("仅学生可以查看自己的教学班")
        return self.db.fetch_all("""SELECT c.class_id,c.course_id,c.class_name,k.course_name FROM classes c
            JOIN courses k USING(course_id) JOIN class_memberships m USING(class_id)
            JOIN course_enrollments e ON e.course_id=c.course_id AND e.student_id=m.student_id
            WHERE m.student_id=? AND m.status='active' AND c.status='active' AND k.course_type='shared_course'
            ORDER BY k.course_name,c.class_name""", (actor["user_id"],))

    def sources(self, actor, course_id, class_id):
        self.scope(actor, course_id, class_id)
        if actor["role"] != "teacher":
            raise PermissionDenied("仅教师可以选择任务题目")
        versions = self.db.fetch_all("""SELECT v.version_id,v.version_number,f.folder_name FROM question_bank_versions v
            LEFT JOIN question_bank_folders f USING(folder_id) WHERE v.course_id=? AND v.status='published'""", (course_id,))
        for version in versions:
            version["items"] = self._source_items(version["version_id"], class_id)
        return versions

    def _source_items(self, version_id, class_id):
        result = []
        for row in self.db.fetch_all("SELECT snapshot_json FROM question_bank_version_items WHERE version_id=? AND snapshot_json IS NOT NULL", (version_id,)):
            item = json.loads(row["snapshot_json"])
            if item.get("class_ids") and class_id not in item["class_ids"]:
                continue
            if item.get("question_type") not in {"single_choice", "multiple_choice", "true_false"}:
                continue
            result.append(item)
        return result

    def publish(self, actor, course_id, class_id, title, kind, version_id, due_at, items):
        self.scope(actor, course_id, class_id)
        if actor["role"] != "teacher":
            raise PermissionDenied("仅教师可以发布任务")
        now, due = self.clock(), timestamp(due_at)
        if kind not in {"homework", "exam"} or not isinstance(title, str) or not title.strip() or len(title) > 160:
            raise ValidationError("任务名称或类型无效")
        if due <= now:
            raise ValidationError("截止时间必须晚于发布时间")
        if not isinstance(items, list) or not 1 <= len(items) <= 100:
            raise ValidationError("任务需包含 1 至 100 道客观题")
        # One SQLite transaction captures the publication and class membership together.
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.scope(actor, course_id, class_id)
            version = conn.execute("SELECT 1 FROM question_bank_versions WHERE version_id=? AND course_id=? AND status='published'", (version_id, course_id)).fetchone()
            if not version:
                raise ValidationError("请选择当前课程已发布的题库版本")
            available = {q["item_id"]: q for q in self._source_items(version_id, class_id)}
            snapshots, seen = [], set()
            for selected in items:
                iid = selected.get("item_id")
                if iid not in available or iid in seen:
                    raise ValidationError("题目不可用、重复或不属于该班级")
                points = selected.get("points", 1)
                if isinstance(points, bool) or not isinstance(points, (float, int)) or not math.isfinite(points) or not 0.01 <= points <= 1000:
                    raise ValidationError("每题分值必须在 0.01 至 1000 之间")
                item = available[iid]
                snapshot = {"item_id": iid, "question_type": item["question_type"], "stem": item["stem_markdown"],
                    "options": json.loads(item.get("options_json") or "[]"), "answer": json.loads(item["correct_answer_json"]),
                    "knowledge_points": json.loads(item.get("knowledge_points_json") or "[]"), "points": float(points)}
                if not self._validate_response(snapshot, snapshot["answer"]):
                    raise ValidationError("题目缺少可判分的标准答案，请重新审核题库")
                snapshots.append(snapshot)
                seen.add(iid)
            roster = [r[0] for r in conn.execute("""SELECT m.student_id FROM class_memberships m
                JOIN users u ON u.user_id=m.student_id WHERE m.class_id=? AND m.status='active'
                AND u.status='active' AND u.role='student' ORDER BY m.student_id""", (class_id,))]
            if not roster:
                raise ValidationError("教学班没有有效学生，无法发布任务")
            task_id = "task_" + uuid4().hex
            conn.execute("INSERT INTO class_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)", (task_id, course_id, class_id,
                actor["user_id"], title.strip(), kind, version_id, encode(snapshots), encode(roster), stamp(now), stamp(due)))
        return self.detail(actor, task_id)

    def raw_task(self, task_id):
        task = self.db.fetch_one("SELECT * FROM class_tasks WHERE task_id=?", (task_id,))
        if not task:
            raise NotFound("任务不存在")
        return task

    def require_task(self, actor, task_id):
        task = self.raw_task(task_id)
        self.scope(actor, task["course_id"], task["class_id"])
        if actor["role"] == "student" and actor["user_id"] not in json.loads(task["roster_json"]):
            raise PermissionDenied("不在任务发布时的应完成人员名单中")
        return task

    def detail(self, actor, task_id):
        raw = self.require_task(actor, task_id)
        task = {k: v for k, v in raw.items() if k not in {"items_json", "roster_json"}}
        task["expected_count"] = len(json.loads(raw["roster_json"]))
        task["items"] = [{k: v for k, v in q.items() if k != "answer"} for q in json.loads(raw["items_json"])]
        task["submissions"] = self.db.fetch_all("""SELECT submission_id,submitted_at,complete,answered,score,total
            FROM class_task_submissions WHERE task_id=? AND student_id=? ORDER BY submission_id""", (task_id, actor["user_id"])) if actor["role"] == "student" else []
        return task

    def list_tasks(self, actor, course_id, class_id):
        self.scope(actor, course_id, class_id)
        tasks = self.db.fetch_all("SELECT * FROM class_tasks WHERE course_id=? AND class_id=? ORDER BY published_at DESC,task_id", (course_id, class_id))
        return [self.detail(actor, t["task_id"]) for t in tasks if actor["role"] == "teacher" or actor["user_id"] in json.loads(t["roster_json"])]

    def submit(self, actor, task_id, request_id, responses):
        if actor.get("role") != "student":
            raise PermissionDenied("仅学生可以提交任务")
        task = self.require_task(actor, task_id)
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100 or not isinstance(responses, dict):
            raise ValidationError("提交标识或答案格式无效")
        items = json.loads(task["items_json"])
        if set(responses) - {q["item_id"] for q in items}:
            raise ValidationError("答案包含不属于本任务的题目")
        supplied = encode(responses)
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.require_task(actor, task_id)
            old = conn.execute("SELECT * FROM class_task_submissions WHERE task_id=? AND student_id=? AND request_id=?", (task_id, actor["user_id"], request_id)).fetchone()
            if old:
                if old["responses_json"] != supplied:
                    raise ValidationError("同一提交标识不能用于不同答案")
                return self._receipt(dict(old), task)
            now = self.clock()
            if now < timestamp(task["published_at"]):
                raise ValidationError("任务尚未开放")
            if task["kind"] == "exam":
                if now > timestamp(task["due_at"]):
                    raise ValidationError("考试已截止")
                if conn.execute("SELECT 1 FROM class_task_submissions WHERE task_id=? AND student_id=?", (task_id, actor["user_id"])).fetchone():
                    raise ValidationError("考试仅允许一次正式提交")
            records = []
            for q in items:
                answer = responses.get(q["item_id"])
                valid = self._validate_response(q, answer)
                correct = valid and QuestionBankService._is_correct(q["question_type"], answer, q["answer"])
                records.append({"item_id": q["item_id"], "answered": valid, "correct": bool(correct),
                    "points": q["points"], "earned": q["points"] if correct else 0, "knowledge_points": q["knowledge_points"]})
            answered = sum(r["answered"] for r in records)
            earned, total = sum(r["earned"] for r in records), sum(q["points"] for q in items)
            cursor = conn.execute("""INSERT INTO class_task_submissions(task_id,student_id,request_id,responses_json,
                records_json,submitted_at,complete,answered,score,total) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (task_id, actor["user_id"], request_id, supplied, encode(records), stamp(now), int(answered == len(items)), answered, 100 * earned / total, total))
            row = dict(conn.execute("SELECT * FROM class_task_submissions WHERE submission_id=?", (cursor.lastrowid,)).fetchone())
            conn.execute("UPDATE student_portrait_evaluations SET invalidated=1 WHERE course_id=? AND class_id=?", (task["course_id"], task["class_id"]))
        return self._receipt(row, task)

    @staticmethod
    def _validate_response(q, answer):
        if answer is None or answer == "" or answer == []:
            return False
        if q["question_type"] == "true_false":
            if not isinstance(answer, str) or answer.upper() not in {"Y", "N", "T", "F", "TRUE", "FALSE", "对", "错", "正确", "错误"}:
                raise ValidationError("判断题答案格式无效")
        else:
            options = q["options"]
            allowed = set(options) if isinstance(options, dict) else {str(o.get("key", o.get("label", ""))) if isinstance(o, dict) else chr(65 + i) for i, o in enumerate(options)}
            chosen = answer if q["question_type"] == "multiple_choice" else [answer]
            if not isinstance(chosen, list) or not chosen or any(not isinstance(a, str) or a not in allowed for a in chosen) or len(set(chosen)) != len(chosen):
                raise ValidationError("请选择题目提供的有效选项")
        return True

    @staticmethod
    def _receipt(row, task):
        return {k: row[k] for k in ("submission_id", "submitted_at", "complete", "answered", "score", "total")} | {"late": timestamp(row["submitted_at"]) > timestamp(task["due_at"])}
