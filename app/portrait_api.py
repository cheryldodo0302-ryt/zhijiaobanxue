"""HTTP adapter for class tasks and portraits; all scope checks live in services."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from campus_service import CampusError, PermissionDenied, NotFound
from class_task_service import ClassTaskService
from student_portrait_service import StudentPortraitService
from student_todo_service import StudentTodoService


class TaskItem(BaseModel):
    item_id: str
    points: float = Field(default=1, ge=0.01, le=1000, allow_inf_nan=False)


class TaskPublish(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    kind: str
    version_id: str
    due_at: str
    items: list[TaskItem] = Field(min_length=1, max_length=100)
    max_submissions: int | None = 1


class TaskSubmit(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)
    responses: dict = Field(default_factory=dict)


class TodoCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class TodoCompletion(BaseModel):
    completed: bool


class SharingScope(BaseModel):
    course_id: str
    class_id: str


class PortraitPeriod(BaseModel):
    start_at: str
    end_at: str


def checked(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except CampusError as exc:
        code = 403 if isinstance(exc, PermissionDenied) else 404 if isinstance(exc, NotFound) else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc


def portrait_router(campus, study_room, current_teacher, current_student):
    router = APIRouter(prefix="/api/v1")
    tasks = ClassTaskService(campus)
    portraits = StudentPortraitService(campus, study_room)
    todos = StudentTodoService(campus)

    @router.get("/student/todos")
    def list_todos(user: dict = Depends(current_student)):
        return checked(todos.list_items, user)

    @router.post("/student/todos")
    def create_todo(payload: TodoCreate, user: dict = Depends(current_student)):
        return checked(todos.create, user, payload.title)

    @router.patch("/student/todos/{todo_id}")
    def complete_todo(todo_id: str, payload: TodoCompletion, user: dict = Depends(current_student)):
        return checked(todos.set_completed, user, todo_id, payload.completed)

    @router.delete("/student/todos/{todo_id}")
    def delete_todo(todo_id: str, user: dict = Depends(current_student)):
        return checked(todos.delete, user, todo_id)

    @router.get("/student/task-scopes")
    def scopes(user: dict = Depends(current_student)):
        return checked(tasks.student_scopes, user)

    @router.get("/teacher/courses/{course_id}/classes/{class_id}/task-sources")
    def sources(course_id: str, class_id: str, user: dict = Depends(current_teacher)):
        return checked(tasks.sources, user, course_id, class_id)

    @router.post("/teacher/courses/{course_id}/classes/{class_id}/tasks")
    def publish(course_id: str, class_id: str, payload: TaskPublish, user: dict = Depends(current_teacher)):
        return checked(tasks.publish, user, course_id, class_id, **payload.model_dump())

    @router.get("/teacher/courses/{course_id}/classes/{class_id}/tasks")
    def teacher_tasks(course_id: str, class_id: str, user: dict = Depends(current_teacher)):
        return checked(tasks.list_tasks, user, course_id, class_id)

    @router.get("/student/courses/{course_id}/classes/{class_id}/tasks")
    def student_tasks(course_id: str, class_id: str, user: dict = Depends(current_student)):
        return checked(tasks.list_tasks, user, course_id, class_id)

    @router.post("/student/tasks/{task_id}/submissions")
    def submit(task_id: str, payload: TaskSubmit, user: dict = Depends(current_student)):
        return checked(tasks.submit, user, task_id, **payload.model_dump())

    @router.get("/student/study-room/grants")
    def grants(user: dict = Depends(current_student)):
        return checked(study_room.grants, user)

    @router.post("/student/study-room/grants")
    def grant(payload: SharingScope, user: dict = Depends(current_student)):
        return checked(study_room.grant, user, payload.course_id, payload.class_id)

    @router.delete("/student/study-room/grants/{grant_id}")
    def revoke(grant_id: str, user: dict = Depends(current_student)):
        checked(study_room.revoke, user, grant_id)
        return {"revoked": True}

    @router.get("/teacher/courses/{course_id}/classes/{class_id}/portraits")
    def roster(course_id: str, class_id: str, start_at: str, end_at: str, user: dict = Depends(current_teacher)):
        return checked(portraits.list_portraits, user, course_id, class_id, start_at, end_at)

    @router.get("/teacher/courses/{course_id}/classes/{class_id}/portraits/{student_id}")
    def portrait(course_id: str, class_id: str, student_id: str, start_at: str, end_at: str, user: dict = Depends(current_teacher)):
        return checked(portraits.get, user, course_id, class_id, student_id, start_at, end_at)

    @router.post("/teacher/courses/{course_id}/classes/{class_id}/portraits/{student_id}/evaluate")
    def evaluate(course_id: str, class_id: str, student_id: str, payload: PortraitPeriod, user: dict = Depends(current_teacher)):
        return checked(portraits.evaluate, user, course_id, class_id, student_id, payload.start_at, payload.end_at)

    return router
