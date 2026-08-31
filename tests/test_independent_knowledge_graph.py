import io
from pathlib import Path

import pytest

from auth_service import AuthService
from campus_service import CampusService, PermissionDenied
from database import LearningDatabase
from knowledge_graph_service import KnowledgeGraphService
from teacher_service import TeacherService


SAMPLE_ROOT = Path(r"F:\我的桌面\wmu事务\人工智能\智慧伴学资料\智慧伴学资料\知识图谱")


def graph_scope(tmp_path: Path):
    db = LearningDatabase(tmp_path / "graph.db")
    campus = CampusService(db, tmp_path / "uploads", provider_factory=lambda: None)
    auth = AuthService(db, tmp_path / "secret")
    teacher = auth.create_user("graph-teacher", "safe-password-123", "teacher")
    student = auth.create_user("graph-student", "safe-password-123", "student")
    outsider = auth.create_user("graph-outsider", "safe-password-123", "student")
    course = TeacherService(db, campus).create_course(teacher, "数据库原理与应用")
    db.execute("INSERT INTO course_enrollments(course_id,student_id) VALUES(?,?)",
               (course["course_id"], student["user_id"]))
    return db, teacher, student, outsider, course, KnowledgeGraphService(db, campus)


@pytest.mark.skipif(not SAMPLE_ROOT.is_dir(), reason="knowledge graph sample folder unavailable")
def test_sample_graph_import_publish_and_student_isolation(tmp_path: Path):
    _db, teacher, student, outsider, course, service = graph_scope(tmp_path)
    batch = service.create_import_batch(teacher, course["course_id"], "样本知识图谱")
    for path in sorted(SAMPLE_ROOT.glob("*.xlsx")):
        with path.open("rb") as source:
            service.add_import_file(
                teacher, batch["batch_id"], path.name,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                source, relative_path=f"知识图谱/{path.name}",
            )
    committed = service.commit_import_batch(teacher, batch["batch_id"])
    assert committed["node_count"] == 56
    assert committed["relation_count"] == 62
    workbench = service.workbench(teacher, course["course_id"])
    assert len([node for node in workbench["nodes"] if node["origin"] == "file"]) == 56
    assert len(workbench["relations"]) == 62
    assert {relation["relation_kind"] for relation in workbench["relations"]} == {
        "part_of", "prerequisite", "progression", "parallel",
    }
    version = service.publish(teacher, course["course_id"])
    assert version["version_number"] == 1
    published = service.student_graph(student, course["course_id"])
    assert published["version"]["graph_version_id"] == version["graph_version_id"]
    with pytest.raises(PermissionDenied):
        service.student_graph(outsider, course["course_id"])


def test_approved_knowledge_import_uses_confirmed_snapshot_sync(tmp_path: Path):
    db, teacher, student, _outsider, course, service = graph_scope(tmp_path)
    node_id = "kn_approved_source"
    db.execute(
        """INSERT INTO knowledge_nodes(
               node_id,course_id,node_scope,node_type,title,summary,markdown,status,content_domain,material_type
           ) VALUES(?,?,'course','knowledge_point','事务管理','原摘要','原正文','approved','knowledge','textbook')""",
        (node_id, course["course_id"]),
    )
    assert service.import_approved_nodes(teacher, course["course_id"], [node_id])["imported"] == 1
    service.publish(teacher, course["course_id"])
    graph_node = service.workbench(teacher, course["course_id"])["nodes"][0]
    db.execute("UPDATE knowledge_nodes SET summary='更新摘要',markdown='更新正文' WHERE node_id=?", (node_id,))
    assert service.source_diff(teacher, course["course_id"])[0]["state"] == "changed"
    published = service.student_graph(student, course["course_id"])
    assert published["nodes"][0]["summary"] == "原摘要"
    assert published["nodes"][0]["markdown"] == ""
    assert service.sync_sources(teacher, course["course_id"], [graph_node["graph_node_id"]])["synced"] == 1
    assert service.workbench(teacher, course["course_id"])["nodes"][0]["summary"] == "更新摘要"


def test_graph_rejects_non_spreadsheet_upload(tmp_path: Path):
    _db, teacher, _student, _outsider, course, service = graph_scope(tmp_path)
    batch = service.create_import_batch(teacher, course["course_id"])
    with pytest.raises(Exception, match="只接受 XLS 或 XLSX"):
        service.add_import_file(
            teacher, batch["batch_id"], "payload.exe", "application/octet-stream",
            io.BytesIO(b"MZ"), relative_path="图谱/payload.exe",
        )
