import io
import os
from pathlib import Path

import pytest

from auth_service import AuthService
from campus_service import CampusService, PermissionDenied, ValidationError
from database import LearningDatabase
from knowledge_graph_service import KnowledgeGraphService
from teacher_service import TeacherService


SAMPLE_ROOT = Path(os.environ.get(
    "ZHIJIAO_GRAPH_SAMPLE_ROOT",
    Path(__file__).resolve().parents[3] / "智慧伴学资料" / "智慧伴学资料" / "知识图谱",
))


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


def test_new_approved_knowledge_is_a_syncable_graph_difference(tmp_path: Path):
    db, teacher, _student, _outsider, course, service = graph_scope(tmp_path)
    node_id = "kn_new_approved_source"
    db.execute(
        """INSERT INTO knowledge_nodes(
               node_id,course_id,node_scope,node_type,title,summary,markdown,status,content_domain,material_type
           ) VALUES(?,?,'course','knowledge_point','新增知识点','摘要','正文','approved','knowledge','textbook')""",
        (node_id, course["course_id"]),
    )
    db.execute(
        """INSERT INTO knowledge_versions(
               version_id,course_id,version_number,status,created_by,published_at
           ) VALUES(?,?,1,'published',?,CURRENT_TIMESTAMP)""",
        ("kv_published_knowledge", course["course_id"], teacher["user_id"]),
    )
    db.execute(
        "INSERT INTO knowledge_version_nodes(version_id,node_id) VALUES(?,?)",
        ("kv_published_knowledge", node_id),
    )
    published = service.workbench(teacher, course["course_id"])["published_knowledge"]
    assert published[0]["title"] == "新增知识点"
    assert published[0]["state"] == "not_in_graph"
    diff = service.source_diff(teacher, course["course_id"])
    assert diff == [{
        "graph_node_id": None,
        "title": "新增知识点",
        "source_knowledge_node_id": node_id,
        "state": "new",
    }]
    result = service.sync_sources(
        teacher, course["course_id"], source_knowledge_node_ids=[node_id]
    )
    assert result["imported"] == 1
    assert service.source_diff(teacher, course["course_id"])[0]["state"] == "current"
    assert service.workbench(teacher, course["course_id"])["published_knowledge"][0]["state"] == "synced"


def test_graph_rejects_non_spreadsheet_upload(tmp_path: Path):
    _db, teacher, _student, _outsider, course, service = graph_scope(tmp_path)
    batch = service.create_import_batch(teacher, course["course_id"])
    with pytest.raises(Exception, match="只接受 XLS 或 XLSX"):
        service.add_import_file(
            teacher, batch["batch_id"], "payload.exe", "application/octet-stream",
            io.BytesIO(b"MZ"), relative_path="图谱/payload.exe",
        )


def test_same_title_sources_sync_independently_and_stay_synced(tmp_path: Path):
    db, teacher, student, _, course, service = graph_scope(tmp_path)
    course_id = course["course_id"]
    for source_id, title in [("source-a", "6.4 逻辑结构设计"), ("source-b", "6.4逻辑结构设计")]:
        db.execute(
            """INSERT INTO knowledge_nodes(node_id,course_id,node_scope,node_type,title,markdown,status)
               VALUES(?,?,'course','knowledge_point',?,?,'approved')""",
            (source_id, course_id, title, source_id),
        )
    service.import_approved_nodes(teacher, course_id, ["source-a"])
    original = service.workbench(teacher, course_id)["nodes"][0]
    service.publish(teacher, course_id)
    for _ in range(3):
        diff = service.source_diff(teacher, course_id)
        service.sync_sources(teacher, course_id, [], [r["source_knowledge_node_id"] for r in diff if r["state"] == "new"])
        service.import_approved_nodes(teacher, course_id)
        assert {r["state"] for r in service.source_diff(teacher, course_id)} == {"current"}
        workbench = service.workbench(teacher, course_id)
        assert len(workbench["nodes"]) == 2
        assert {r["state"] for r in workbench["library_knowledge"]} == {"synced"}
    by_source = {r["source_knowledge_node_id"]: r for r in workbench["nodes"]}
    assert by_source["source-a"]["graph_node_id"] == original["graph_node_id"]
    assert by_source["source-a"]["markdown"] == "source-a"
    assert by_source["source-b"]["markdown"] == "source-b"
    assert len(service.student_graph(student, course_id)["nodes"]) == 1
    db.execute("UPDATE knowledge_nodes SET markdown='updated' WHERE node_id='source-b'")
    service.sync_sources(teacher, course_id, [by_source["source-b"]["graph_node_id"]])
    assert {r["state"] for r in service.source_diff(teacher, course_id)} == {"current"}
    service.update_node(teacher, by_source["source-b"]["graph_node_id"], {"notes": "审核备注"})
    assert len(service.workbench(teacher, course_id)["nodes"]) == 2


def test_source_rename_to_existing_title_preserves_both_nodes(tmp_path: Path):
    db, teacher, _, _, course, service = graph_scope(tmp_path)
    course_id = course["course_id"]
    for source_id in ["rename-a", "rename-b"]:
        db.execute(
            """INSERT INTO knowledge_nodes(node_id,course_id,node_scope,node_type,title,status)
               VALUES(?,?,'course','knowledge_point',?,'approved')""",
            (source_id, course_id, source_id),
        )
    service.import_approved_nodes(teacher, course_id)
    before = {r["graph_node_id"] for r in service.workbench(teacher, course_id)["nodes"]}
    db.execute("UPDATE knowledge_nodes SET title='rename-a' WHERE node_id='rename-b'")
    service.sync_sources(teacher, course_id)
    service.import_approved_nodes(teacher, course_id)
    assert {r["graph_node_id"] for r in service.workbench(teacher, course_id)["nodes"]} == before
    assert {r["state"] for r in service.source_diff(teacher, course_id)} == {"current"}


def test_library_sync_keeps_node_identity_and_requires_approved_sources(tmp_path: Path):
    db, teacher, student, _, course, service = graph_scope(tmp_path)
    db.execute(
        """INSERT INTO knowledge_nodes(node_id,course_id,node_scope,node_type,title,markdown,status)
           VALUES('library-source',?,'course','knowledge_point','原名称','原正文','approved')""",
        (course["course_id"],),
    )
    assert service.workbench(teacher, course["course_id"])["library_knowledge"][0]["title"] == "原名称"
    service.import_approved_nodes(teacher, course["course_id"])
    service.publish(teacher, course["course_id"])
    db.execute("UPDATE knowledge_nodes SET title='新名称',status='draft' WHERE node_id='library-source'")
    with pytest.raises(ValidationError, match="入库"):
        service.publish(teacher, course["course_id"])
    with pytest.raises(ValidationError):
        service.sync_sources(teacher, course["course_id"], source_knowledge_node_ids=["library-source"])
    assert service.student_graph(student, course["course_id"])["nodes"][0]["title"] == "原名称"
    db.execute("UPDATE knowledge_nodes SET status='approved' WHERE node_id='library-source'")
    with pytest.raises(ValidationError, match="同步"):
        service.publish(teacher, course["course_id"])
    service.import_approved_nodes(teacher, course["course_id"])
    assert len(service.workbench(teacher, course["course_id"])["nodes"]) == 1
    assert service.student_graph(student, course["course_id"])["nodes"][0]["title"] == "原名称"
    service.publish(teacher, course["course_id"])
    assert service.student_graph(student, course["course_id"])["nodes"][0]["title"] == "新名称"
