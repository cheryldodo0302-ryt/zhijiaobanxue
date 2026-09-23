"""Private student todos and read-only projections of formal class tasks."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from campus_service import NotFound, PermissionDenied, ValidationError
from class_task_service import ClassTaskService, timestamp


class StudentTodoService:
    def __init__(self, campus):
        self.db = campus.db
        self.tasks = ClassTaskService(campus)

    @staticmethod
    def _student(actor):
        if actor.get("role") != "student" or not actor.get("user_id"):
            raise PermissionDenied("仅学生可以管理自己的待办")
        return str(actor["user_id"])

    def list_items(self, actor):
        student_id = self._student(actor)
        own = self.db.fetch_all(
            """SELECT todo_id,title,completed_at,created_at,updated_at FROM student_todos
               WHERE student_id=? ORDER BY created_at DESC,todo_id DESC""", (student_id,),
        )
        items = [{"id": "personal:" + row["todo_id"], "source": "personal",
                  "title": row["title"], "state": "completed" if row["completed_at"] else "pending",
                  "completed_at": row["completed_at"], "created_at": row["created_at"]}
                 for row in own]
        now = datetime.now(timezone.utc)
        for scope in self.tasks.student_scopes(actor):
            for task in self.tasks.list_tasks(actor, scope["course_id"], scope["class_id"]):
                complete = any(row["complete"] for row in task["submissions"])
                closed = task["remaining_submissions"] == 0 or (
                    task["kind"] == "exam" and timestamp(task["due_at"]) < now
                )
                items.append({
                    "id": "class_task:" + task["task_id"], "source": "class_task",
                    "title": task["title"], "state": "completed" if complete else "closed" if closed else "pending",
                    "kind": task["kind"], "course_id": scope["course_id"],
                    "course_name": scope["course_name"], "class_id": scope["class_id"],
                    "class_name": scope["class_name"], "task_id": task["task_id"],
                    "due_at": task["due_at"], "submission_count": task["submission_count"],
                    "remaining_submissions": task["remaining_submissions"],
                })
        order = {"pending": 0, "closed": 1, "completed": 2}
        items.sort(key=lambda item: (
            order[item["state"]], 0 if item["source"] == "class_task" else 1,
            item.get("due_at") or "9999", item["id"],
        ))
        return items

    def create(self, actor, title):
        student_id = self._student(actor)
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
            raise ValidationError("待办内容须为 1 至 120 个字符")
        todo_id = "todo_" + uuid4().hex
        with self.db.connect() as conn:
            conn.execute("INSERT INTO student_todos(todo_id,student_id,title) VALUES(?,?,?)",
                         (todo_id, student_id, title.strip()))
        return {"todo_id": todo_id}

    def set_completed(self, actor, todo_id, completed):
        student_id = self._student(actor)
        if not isinstance(completed, bool):
            raise ValidationError("完成状态无效")
        with self.db.connect() as conn:
            cursor = conn.execute(
                """UPDATE student_todos SET completed_at=?,updated_at=CURRENT_TIMESTAMP
                   WHERE todo_id=? AND student_id=?""",
                (datetime.now(timezone.utc).isoformat() if completed else None, todo_id, student_id),
            )
            if not cursor.rowcount:
                raise NotFound("待办不存在")
        return {"todo_id": todo_id, "completed": completed}

    def delete(self, actor, todo_id):
        student_id = self._student(actor)
        with self.db.connect() as conn:
            cursor = conn.execute("DELETE FROM student_todos WHERE todo_id=? AND student_id=?",
                                  (todo_id, student_id))
            if not cursor.rowcount:
                raise NotFound("待办不存在")
        return {"deleted": todo_id}
