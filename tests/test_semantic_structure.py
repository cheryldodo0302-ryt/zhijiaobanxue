import json
import builtins

from auth_service import AuthService
from campus_service import CampusService
from config import student_import_config_status
from database import LearningDatabase
from ingestion_service import IngestionService
from teacher_service import TeacherService


class StructuralProvider:
    def generate(self, _system, prompt):
        payload = json.loads(prompt)
        if "candidates" in payload:
            return json.dumps({"points": [{
                "point_key": f"r{index}", "chapter": item["chapter"], "section": item["section"],
                "title": item["title"], "keywords": item["keywords"],
                "source_candidate_ids": [item["candidate_id"]],
            } for index, item in enumerate(payload["candidates"])]}, ensure_ascii=False)
        if "nodes" in payload:
            nodes = [node for node in payload["nodes"] if node["title"]]
            if len(nodes) < 2:
                return json.dumps({"relations": []})
            return json.dumps({"relations": [{
                "source_node_id": nodes[0]["node_id"], "target_node_id": nodes[1]["node_id"],
                "type": relation_type, "confidence": .9, "reason": "测试",
            } for relation_type in ("parallel", "prerequisite", "follow_up", "related", "confusable")]}, ensure_ascii=False)
        if "source_points" in payload:
            source = payload["source_points"]
            points = [{
                "course_key": f"c{index}", "chapter": "第一章", "section": "第一节",
                "title": item["title"], "keywords": item["keywords"],
                "source_node_ids": [item["source_node_id"]],
            } for index, item in enumerate(source)]
            relations = []
            if len(points) >= 2:
                for relation_type in ("parallel", "prerequisite", "follow_up", "related", "confusable"):
                    relations.append({"source_course_key": "c0", "target_course_key": "c1",
                                      "type": relation_type, "confidence": .9, "reason": "测试"})
            return json.dumps({"points": points, "relations": relations}, ensure_ascii=False)
        blocks = payload["blocks"]
        classifications = [{"block_id": row["block_id"], "destination": "knowledge",
                            "semantic_role": "definition", "confidence": .9, "reason": "测试"}
                           for row in blocks]
        points = [{"point_key": f"p{index}", "chapter": "第一章", "section": "第一节",
                   "title": f"知识点{index + 1}", "keywords": ["测试"],
                   "block_ids": [row["block_id"]], "evidence_quotes": [row["content"][:30]]}
                  for index, row in enumerate(blocks)]
        return json.dumps({"classifications": classifications, "knowledge_points": points, "relations": []}, ensure_ascii=False)


class OmissionProvider:
    def generate(self, _system, prompt):
        payload = json.loads(prompt)
        if "candidates" in payload:
            candidate = payload["candidates"][0]
            return json.dumps({"points": [{
                "point_key": "reduced-1", "chapter": candidate["chapter"],
                "section": candidate["section"], "title": candidate["title"],
                "keywords": candidate["keywords"],
                "source_candidate_ids": [candidate["candidate_id"]],
            }]}, ensure_ascii=False)
        if "source_points" in payload:
            source = payload["source_points"][0]
            return json.dumps({"points": [{
                "course_key": "course-1", "chapter": source["chapter"],
                "section": source["section"], "title": source["title"],
                "keywords": source["keywords"], "source_node_ids": [source["source_node_id"]],
            }], "relations": []}, ensure_ascii=False)
        blocks = payload["blocks"]
        return json.dumps({
            "classifications": [{
                "block_id": blocks[0]["block_id"], "destination": "knowledge",
                "semantic_role": "definition", "confidence": .8, "reason": "测试遗漏",
            }],
            "knowledge_points": [{
                "point_key": "point-1", "chapter": "第一章", "section": "第一节",
                "title": "保留知识点", "keywords": [], "block_ids": [blocks[0]["block_id"]],
                "evidence_quotes": [blocks[0]["content"][:30]],
            }],
        }, ensure_ascii=False)


def setup(tmp_path):
    db = LearningDatabase(tmp_path / "semantic.db")
    campus = CampusService(db, tmp_path / "uploads", provider_factory=StructuralProvider)
    auth = AuthService(db, tmp_path / "secret")
    teacher = auth.create_user("teacher", "safe-password-123", "teacher")
    teachers = TeacherService(db, campus)
    course = teachers.create_course(teacher, "数据库")
    return db, campus, teacher, course


def test_full_semantic_analysis_builds_both_outlines_and_relations(tmp_path):
    db, campus, teacher, course = setup(tmp_path)
    service = IngestionService(db, campus)
    job = service.queue_document(
        teacher, course["course_id"], "lesson.md", "text/markdown",
        "# 第一章 原样章标题\n章原文\n## 1.1 原样节标题\n节原文\n"
        "### 1.1.1 关系模型定义\n定义原文\n### 1.1.2 关系代数定义\n代数原文".encode(),
    )
    service.process_job(job["job_id"])
    analysis = db.fetch_one("SELECT * FROM semantic_analysis_jobs WHERE document_id=?", (job["document_id"],))
    assert analysis["status"] == "queued"
    service.process_semantic_analysis(analysis["analysis_job_id"])
    completed = service.get_analysis_job(teacher, analysis["analysis_job_id"])
    assert completed["status"] == "review_required", completed["error_message"]
    document = service.document_outline(teacher, job["document_id"])
    course_outline = service.course_outline(teacher, course["course_id"])
    assert {node["node_type"] for node in document["nodes"]} == {"chapter", "section", "knowledge_point"}
    assert all("summary" not in node for node in document["nodes"])
    source_markdown = "\n".join(
        row["markdown"] for row in db.fetch_all(
            "SELECT markdown FROM document_blocks WHERE document_id=? ORDER BY block_order", (job["document_id"],)
        )
    )
    generated_markdown = "\n".join(
        node["markdown"] for node in document["nodes"] if node["node_type"] == "knowledge_point"
    )
    assert generated_markdown == source_markdown
    document_points = [node for node in document["nodes"] if node["node_type"] == "knowledge_point"]
    course_points = [node for node in course_outline["nodes"] if node["node_type"] == "knowledge_point"]
    assert len(document_points) == len(db.fetch_all(
        "SELECT block_id FROM document_blocks WHERE document_id=?", (job["document_id"],)
    ))
    assert len(course_points) == len(document_points)
    assert {row["relation_type"] for row in course_outline["relations"]} == {
        "parallel", "prerequisite", "follow_up", "related", "confusable",
    }
    for node in course_points:
        service.update_node(teacher, node["node_id"], {"status": "approved"})
    version = service.publish(teacher, course["course_id"])
    assert all(node["title"] in version["markdown_snapshot"] for node in course_points)
    assert len(campus._retriever(course["course_id"]).rows) == len(course_points)

    second = service.queue_semantic_analysis(teacher, job["document_id"])
    service.process_semantic_analysis(second["analysis_job_id"])
    count = db.fetch_one("SELECT COUNT(*) n FROM knowledge_nodes WHERE document_id=?", (job["document_id"],))["n"]
    assert count == len(document["nodes"])


def test_short_default_password_is_allowed_and_reported_weak(tmp_path, monkeypatch):
    import config
    monkeypatch.delenv("ZHIJIAO_STUDENT_DEFAULT_PASSWORD", raising=False)
    config_file = tmp_path / "server.env"
    config_file.write_text("ZHIJIAO_STUDENT_DEFAULT_PASSWORD=123\n", encoding="utf-8")
    monkeypatch.setattr(config, "SERVER_ENV", config_file)
    status = student_import_config_status()
    assert status["configured"] is True and status["security_level"] == "weak"


def test_numbered_headings_keep_the_complete_original_title():
    assert IngestionService._heading_from_line("1. 核心定位与可完成对接机会") == (
        1, "1. 核心定位与可完成对接机会",
    )
    assert IngestionService._heading_from_line("1.2 可复用教改思路与竞赛边界") == (
        2, "1.2 可复用教改思路与竞赛边界",
    )


def test_ai_omitted_block_is_kept_for_review_instead_of_failing(tmp_path):
    db, campus, teacher, course = setup(tmp_path)
    campus.provider_factory = OmissionProvider
    service = IngestionService(db, campus)
    job = service.queue_document(
        teacher, course["course_id"], "lesson.md", "text/markdown",
        "# 第一章\n## 1.1 例题\n例题：内容一\n## 1.2 答案\n答案：内容二".encode(),
    )
    service.process_job(job["job_id"])
    analysis = db.fetch_one("SELECT * FROM semantic_analysis_jobs WHERE document_id=?", (job["document_id"],))
    service.process_semantic_analysis(analysis["analysis_job_id"])
    completed = service.get_analysis_job(teacher, analysis["analysis_job_id"])
    assert completed["status"] == "review_required", completed["error_message"]
    blocks = db.fetch_all("SELECT * FROM document_blocks WHERE document_id=?", (job["document_id"],))
    assert any(block["semantic_role"] == "ai_omitted_teacher_review" for block in blocks)


def test_teacher_can_merge_split_and_move_semantic_points(tmp_path):
    db, campus, teacher, course = setup(tmp_path)
    service = IngestionService(db, campus)
    job = service.queue_document(
        teacher, course["course_id"], "lesson.md", "text/markdown",
        "# 第一章\n## 第一节\n### 定义一\n内容一\n### 定义二\n内容二".encode(),
    )
    service.process_job(job["job_id"])
    analysis = db.fetch_one("SELECT * FROM semantic_analysis_jobs WHERE document_id=?", (job["document_id"],))
    service.process_semantic_analysis(analysis["analysis_job_id"])
    outline = service.document_outline(teacher, job["document_id"])
    points = [node for node in outline["nodes"] if node["node_type"] == "knowledge_point"]
    merged = service.merge_nodes(teacher, [point["node_id"] for point in points], "合并知识点")
    parts = service.split_node(teacher, merged["node_id"], [
        {"title": "拆分一", "markdown": "内容一"}, {"title": "拆分二", "markdown": "内容二"},
    ])
    assert [part["title"] for part in parts] == ["拆分一", "拆分二"]


def test_evidence_tree_pipeline_never_imports_torch_or_docling(tmp_path, monkeypatch):
    db, campus, teacher, course = setup(tmp_path)
    service = IngestionService(db, campus)
    job = service.queue_document(
        teacher, course["course_id"], "lesson.md", "text/markdown", b"# Source\nEvidence text"
    )
    service.process_job(job["job_id"])
    analysis = db.fetch_one(
        "SELECT * FROM semantic_analysis_jobs WHERE document_id=?", (job["document_id"],)
    )
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "torch" or name.startswith("torch.") or name == "docling" or name.startswith("docling."):
            raise AssertionError(f"forbidden runtime import: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    service.process_semantic_analysis(analysis["analysis_job_id"])
    completed = service.get_analysis_job(teacher, analysis["analysis_job_id"])
    assert completed["status"] == "review_required", completed["error_message"]
    assert completed["analyzer_version"] == "evidence-map-reduce-v4"


def test_rejecting_course_chapter_hides_all_descendants_and_relations(tmp_path):
    db, campus, teacher, course = setup(tmp_path)
    service = IngestionService(db, campus)
    job = service.queue_document(
        teacher, course["course_id"], "lesson.md", "text/markdown", b"# Source\nEvidence text"
    )
    service.process_job(job["job_id"])
    analysis = db.fetch_one(
        "SELECT * FROM semantic_analysis_jobs WHERE document_id=?", (job["document_id"],)
    )
    service.process_semantic_analysis(analysis["analysis_job_id"])
    outline = service.course_outline(teacher, course["course_id"])
    chapter = next(node for node in outline["nodes"] if node["node_type"] == "chapter")
    service.update_node(teacher, chapter["node_id"], {"status": "rejected"})
    hidden = service.course_outline(teacher, course["course_id"])
    assert hidden["nodes"] == []
    assert hidden["relations"] == []
    statuses = db.fetch_all(
        "SELECT status FROM knowledge_nodes WHERE course_id=? AND node_scope='course'",
        (course["course_id"],),
    )
    assert statuses and {row["status"] for row in statuses} == {"rejected"}
