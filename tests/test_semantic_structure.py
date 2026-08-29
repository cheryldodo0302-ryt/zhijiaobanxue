import json
import builtins

import pytest

from auth_service import AuthService
from campus_service import CampusService, ValidationError
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


class MissingSemanticRoleProvider:
    def generate(self, _system, prompt):
        payload = json.loads(prompt)
        blocks = payload["blocks"]
        return json.dumps({
            "classifications": [{
                "block_id": row["block_id"], "destination": "unclassified",
                "confidence": 0.4, "reason": "需要教师复核",
            } for row in blocks],
            "knowledge_points": [],
        }, ensure_ascii=False)


def semantic_setup(tmp_path):
    db = LearningDatabase(tmp_path / "semantic.db")
    campus = CampusService(db, tmp_path / "uploads", provider_factory=StructuralProvider)
    auth = AuthService(db, tmp_path / "secret")
    teacher = auth.create_user("teacher", "safe-password-123", "teacher")
    teachers = TeacherService(db, campus)
    course = teachers.create_course(teacher, "数据库")
    return db, campus, teacher, course


def test_full_semantic_analysis_builds_both_outlines_and_relations(tmp_path):
    db, campus, teacher, course = semantic_setup(tmp_path)
    service = IngestionService(db, campus)
    job = service.queue_document(
        teacher, course["course_id"], "lesson.md", "text/markdown",
        "# 第一章 原样章标题\n章原文\n## 1.1 原样节标题\n节原文\n"
        "### 1.1.1 关系模型定义\n定义原文\n### 1.1.2 关系代数定义\n代数原文\n"
        "## 1.2 第二节\n### 1.2.1 键的定义\n键原文".encode(),
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
    generated_markdown = "\n".join(
        node["markdown"] for node in document["nodes"] if node["node_type"] == "knowledge_point"
    )
    assert "## 1.1 原样节标题" in generated_markdown
    assert "### 1.1.1 关系模型定义" in generated_markdown
    assert "### 1.1.2 关系代数定义" in generated_markdown
    assert "## 1.2 第二节" in generated_markdown
    assert "# 第一章 原样章标题" not in generated_markdown
    document_points = [node for node in document["nodes"] if node["node_type"] == "knowledge_point"]
    course_points = [node for node in course_outline["nodes"] if node["node_type"] == "knowledge_point"]
    assert [point["title"] for point in document_points] == ["1.1 原样节标题", "1.2 第二节"]
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


def test_course_outline_is_partitioned_and_incremental_review_is_preserved(tmp_path):
    db, campus, teacher, course = semantic_setup(tmp_path)
    service = IngestionService(db, campus)

    def analyze(name: str, material_type: str, body: str) -> dict:
        job = service.queue_document(
            teacher, course["course_id"], name, "text/markdown", body.encode("utf-8")
        )
        service.process_job(job["job_id"])
        service.update_material_metadata(
            teacher, job["document_id"], material_type=material_type, tags=[]
        )
        analysis = db.fetch_one(
            "SELECT * FROM semantic_analysis_jobs WHERE document_id=?",
            (job["document_id"],),
        )
        service.process_semantic_analysis(analysis["analysis_job_id"])
        return job

    analyze("教材第一章.md", "textbook", "# 第一章\n## 1.1 同名内容\n教材证据")
    textbook_outline = service.course_outline(
        teacher, course["course_id"], material_type="textbook"
    )
    textbook_point = next(
        node for node in textbook_outline["nodes"] if node["node_type"] == "knowledge_point"
    )
    service.update_node(teacher, textbook_point["node_id"], {"status": "approved"})

    first_slide_job = analyze("课件第一章.md", "slides", "# 第一章\n## 1.1 同名内容\n课件证据")
    first = service.course_outline(teacher, course["course_id"])
    assert {item["material_type"] for item in first["partitions"]} == {"slides", "textbook"}
    assert {
        node["material_type"] for node in first["nodes"] if node["node_type"] == "knowledge_point"
    } == {"slides", "textbook"}
    preserved = next(
        node for node in first["nodes"]
        if node["node_type"] == "knowledge_point" and node["material_type"] == "textbook"
    )
    assert preserved["status"] == "approved"

    slide_point = next(
        node for node in first["nodes"]
        if node["node_type"] == "knowledge_point" and node["material_type"] == "slides"
    )
    service.update_node(teacher, slide_point["node_id"], {"status": "approved"})
    analyze("课件第二章.md", "slides", "# 第二章\n## 2.1 新课件\n第二章证据")
    second = service.course_outline(teacher, course["course_id"])
    unchanged_slide = next(
        node for node in second["nodes"]
        if node["node_type"] == "knowledge_point" and node["material_type"] == "slides"
        and "1.1" in node["title"]
    )
    assert unchanged_slide["status"] == "approved"
    assert all(
        relation["material_type"] in {"slides", "textbook"}
        for relation in second["relations"]
    )
    for relation in second["relations"]:
        source = next(node for node in second["nodes"] if node["node_id"] == relation["source_node_id"])
        target = next(node for node in second["nodes"] if node["node_id"] == relation["target_node_id"])
        assert source["material_type"] == target["material_type"] == relation["material_type"]

    with pytest.raises(ValidationError, match="同一目录范围"):
        service.merge_nodes(
            teacher,
            [preserved["node_id"], unchanged_slide["node_id"]],
            "禁止跨材料合并",
        )

    changed = service.update_material_metadata(
        teacher, first_slide_job["document_id"], material_type="textbook", tags=[]
    )
    assert set(changed["affected_material_types"]) == {"slides", "textbook"}
    rebuild = service.rebuild_material_partitions(
        course["course_id"], changed["affected_material_types"]
    )
    assert {item["status"] for item in rebuild["partitions"].values()} <= {
        "completed", "safe_fallback"
    }
    reclassified = service.course_outline(teacher, course["course_id"])
    original_textbook = next(
        node for node in reclassified["nodes"]
        if node["node_type"] == "knowledge_point" and node["material_type"] == "textbook"
        and "教材证据" in node["markdown"]
    )
    assert original_textbook["status"] == "approved"
    assert all(
        "课件证据" not in node["markdown"]
        for node in reclassified["nodes"]
        if node["node_type"] == "knowledge_point" and node["material_type"] == "slides"
    )


def test_legacy_leafless_ppt_outline_is_locally_repaired_before_partitioning(tmp_path):
    db, campus, teacher, course = semantic_setup(tmp_path)
    service = IngestionService(db, campus)
    job = service.queue_document(
        teacher, course["course_id"], "第一章.md", "text/markdown",
        (
            "# 第一章 数据库\n## 1.1 数据\n数据的定义与性质\n"
            "## 1.2 信息\n信息是加工后的数据"
        ).encode("utf-8"),
    )
    service.process_job(job["job_id"])
    service.update_material_metadata(
        teacher, job["document_id"], material_type="slides", tags=[]
    )
    local = service.queue_semantic_analysis(
        teacher, job["document_id"], analysis_mode="local"
    )
    service.process_semantic_analysis(local["analysis_job_id"])

    with db.connect() as conn:
        conn.execute(
            """UPDATE document_blocks SET content_destination='unclassified'
               WHERE document_id=? AND content_destination='knowledge'""",
            (job["document_id"],),
        )
        conn.execute(
            "DELETE FROM knowledge_relations WHERE course_id=?",
            (course["course_id"],),
        )
        conn.execute(
            "DELETE FROM knowledge_nodes WHERE course_id=? AND node_scope='course'",
            (course["course_id"],),
        )
        conn.execute(
            "DELETE FROM course_outline_generations WHERE course_id=?",
            (course["course_id"],),
        )
        conn.execute(
            """DELETE FROM knowledge_nodes WHERE document_id=?
               AND node_scope='document' AND node_type='knowledge_point'""",
            (job["document_id"],),
        )

    repaired = service.course_outline(
        teacher, course["course_id"], material_type="slides"
    )
    document_points = db.fetch_one(
        """SELECT COUNT(*) n FROM knowledge_nodes WHERE document_id=?
           AND node_scope='document' AND node_type='knowledge_point'""",
        (job["document_id"],),
    )
    assert int(document_points["n"]) == 2
    assert repaired["partitions"][0]["material_type"] == "slides"
    assert len([
        node for node in repaired["nodes"] if node["node_type"] == "knowledge_point"
    ]) == 2


def test_published_retrieval_can_filter_material_partition(tmp_path):
    db, campus, teacher, course = semantic_setup(tmp_path)
    service = IngestionService(db, campus)
    for name, material_type, evidence in (
        ("教材.md", "textbook", "教材专属证据"),
        ("课件.md", "slides", "课件专属证据"),
    ):
        job = service.queue_document(
            teacher, course["course_id"], name, "text/markdown",
            f"# 第一章\n## 1.1 {evidence}\n{evidence}".encode("utf-8"),
        )
        service.process_job(job["job_id"])
        service.update_material_metadata(
            teacher, job["document_id"], material_type=material_type, tags=[]
        )
        analysis = db.fetch_one(
            "SELECT analysis_job_id FROM semantic_analysis_jobs WHERE document_id=?",
            (job["document_id"],),
        )
        service.process_semantic_analysis(analysis["analysis_job_id"])

    outline = service.course_outline(teacher, course["course_id"])
    for point in (node for node in outline["nodes"] if node["node_type"] == "knowledge_point"):
        service.update_node(teacher, point["node_id"], {"status": "approved"})
    service.publish(teacher, course["course_id"])

    all_rows = campus._retriever(course["course_id"]).rows
    slide_rows = campus._retriever(course["course_id"], "slides").rows
    textbook_rows = campus._retriever(course["course_id"], "textbook").rows
    assert {row["material_type"] for row in all_rows} == {"slides", "textbook"}
    assert {row["material_type"] for row in slide_rows} == {"slides"}
    assert {row["material_type"] for row in textbook_rows} == {"textbook"}
    evidence = campus._retriever(course["course_id"], "slides").search("课件专属证据", 3)
    assert evidence and {item.material_label for item in evidence} == {"课件"}


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
    db, campus, teacher, course = semantic_setup(tmp_path)
    campus.provider_factory = OmissionProvider
    service = IngestionService(db, campus)
    job = service.queue_document(
        teacher, course["course_id"], "lesson.md", "text/markdown",
        "# 第一章\n## 定义一\n内容一\n## 定义二\n内容二".encode(),
    )
    service.process_job(job["job_id"])
    analysis = db.fetch_one("SELECT * FROM semantic_analysis_jobs WHERE document_id=?", (job["document_id"],))
    service.process_semantic_analysis(analysis["analysis_job_id"])
    completed = service.get_analysis_job(teacher, analysis["analysis_job_id"])
    assert completed["status"] == "review_required", completed["error_message"]
    blocks = db.fetch_all("SELECT * FROM document_blocks WHERE document_id=?", (job["document_id"],))
    assert any(block["semantic_role"] == "ai_omitted_teacher_review" for block in blocks)


def test_missing_semantic_role_is_normalized_instead_of_failing_job(tmp_path):
    db, campus, teacher, course = semantic_setup(tmp_path)
    campus.provider_factory = MissingSemanticRoleProvider
    service = IngestionService(db, campus)
    job = service.queue_document(
        teacher, course["course_id"], "missing-role.md", "text/markdown",
        "普通标题\n正文内容".encode("utf-8"),
    )
    service.process_job(job["job_id"])
    analysis = db.fetch_one(
        "SELECT * FROM semantic_analysis_jobs WHERE document_id=?", (job["document_id"],)
    )
    service.process_semantic_analysis(analysis["analysis_job_id"])

    completed = service.get_analysis_job(teacher, analysis["analysis_job_id"])
    assert completed["status"] == "review_required", completed["error_message"]
    blocks = db.fetch_all("SELECT * FROM document_blocks WHERE document_id=?", (job["document_id"],))
    assert blocks and {row["semantic_role"] for row in blocks} == {"teacher_review"}


def test_retry_repairs_legacy_checkpoint_missing_semantic_role(tmp_path):
    db, campus, teacher, course = semantic_setup(tmp_path)
    service = IngestionService(db, campus)
    job = service.queue_document(
        teacher, course["course_id"], "legacy-checkpoint.md", "text/markdown",
        "普通标题\n正文内容".encode("utf-8"),
    )
    service.process_job(job["job_id"])
    analysis = db.fetch_one(
        "SELECT * FROM semantic_analysis_jobs WHERE document_id=?", (job["document_id"],)
    )
    blocks = db.fetch_all(
        "SELECT block_id FROM document_blocks WHERE document_id=? ORDER BY block_order",
        (job["document_id"],),
    )
    checkpoint = {
        "schema_version": 5,
        "extractor": "evidence-map-reduce",
        "map_batch_count": 1,
        "map_results": [{
            "batch": 1,
            "classifications": [{
                "block_id": row["block_id"],
                "destination": "knowledge",
                "reason": "旧版模型返回",
            } for row in blocks],
            "candidates": [],
            "fallback": False,
        }],
        "fallback_batches": [],
    }
    db.execute(
        """UPDATE semantic_analysis_jobs SET status='failed',current_stage='failed',
           current_batch=1,total_batches=3,error_message='semantic_role',result_json=?
           WHERE analysis_job_id=?""",
        (json.dumps(checkpoint, ensure_ascii=False), analysis["analysis_job_id"]),
    )

    service.retry_analysis(teacher, analysis["analysis_job_id"])
    service.process_semantic_analysis(analysis["analysis_job_id"])

    completed = service.get_analysis_job(teacher, analysis["analysis_job_id"])
    assert completed["status"] == "review_required", completed["error_message"]
    assert completed["warnings"] == [
        "1 个旧分析断点字段不完整，已从落库原文安全修复"
    ]
    assert db.fetch_one(
        """SELECT COUNT(*) n FROM knowledge_nodes
           WHERE document_id=? AND node_type='knowledge_point'""",
        (job["document_id"],),
    )["n"] >= 1


def test_teacher_can_merge_split_and_move_semantic_points(tmp_path):
    db, campus, teacher, course = semantic_setup(tmp_path)
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


def test_teacher_can_drag_multiple_points_between_section_folders(tmp_path):
    db, campus, teacher, course = semantic_setup(tmp_path)
    service = IngestionService(db, campus)
    job = service.queue_document(
        teacher, course["course_id"], "move.md", "text/markdown",
        "# 第一章\n## 1.1 第一节\n内容一\n## 1.2 第二节\n内容二".encode("utf-8"),
    )
    service.process_job(job["job_id"])
    analysis = db.fetch_one(
        "SELECT * FROM semantic_analysis_jobs WHERE document_id=?", (job["document_id"],)
    )
    service.process_semantic_analysis(analysis["analysis_job_id"])
    outline = service.document_outline(teacher, job["document_id"])
    sections = [node for node in outline["nodes"] if node["node_type"] == "section"]
    points = [node for node in outline["nodes"] if node["node_type"] == "knowledge_point"]
    assert len(sections) == 2 and len(points) == 2
    original_positions = [
        {
            "node_id": node["node_id"],
            "parent_id": node["parent_id"],
            "sort_order": node["sort_order"],
        }
        for node in outline["nodes"]
    ]

    service.move_nodes(teacher, [points[0]["node_id"]], sections[1]["node_id"], 1)
    service.move_nodes(
        teacher,
        [points[0]["node_id"], points[1]["node_id"]],
        sections[1]["node_id"],
        0,
    )
    moved = service.document_outline(teacher, job["document_id"])["nodes"]
    moved_points = [node for node in moved if node["node_type"] == "knowledge_point"]
    assert {node["parent_id"] for node in moved_points} == {sections[1]["node_id"]}
    assert [node["title"] for node in moved_points] == [points[0]["title"], points[1]["title"]]

    restored = service.restore_node_positions(teacher, original_positions)
    assert restored == {"restored": len(original_positions), "removed": 0, "status": "restored"}
    restored_nodes = service.document_outline(teacher, job["document_id"])["nodes"]
    assert {
        node["node_id"]: (node["parent_id"], node["sort_order"])
        for node in restored_nodes
    } == {
        node["node_id"]: (node["parent_id"], node["sort_order"])
        for node in outline["nodes"]
    }

    promoted = service.move_nodes_as_visible_siblings(
        teacher, [points[0]["node_id"]], sections[1]["node_id"], "before"
    )
    assert promoted["status"] == "promoted"
    assert len(promoted["created_node_ids"]) == 1
    promoted_nodes = service.document_outline(teacher, job["document_id"])["nodes"]
    promoted_wrapper = next(
        node for node in promoted_nodes if node["node_id"] == promoted["created_node_ids"][0]
    )
    promoted_point = next(node for node in promoted_nodes if node["node_id"] == points[0]["node_id"])
    target_section = next(node for node in promoted_nodes if node["node_id"] == sections[1]["node_id"])
    assert promoted_wrapper["node_type"] == "section"
    assert promoted_wrapper["parent_id"] == target_section["parent_id"]
    assert promoted_wrapper["sort_order"] < target_section["sort_order"]
    assert promoted_point["parent_id"] == promoted_wrapper["node_id"]

    undo_promotion = service.restore_node_positions(
        teacher, original_positions, remove_node_ids=promoted["created_node_ids"]
    )
    assert undo_promotion == {
        "restored": len(original_positions), "removed": 1, "status": "restored",
    }
    final_nodes = service.document_outline(teacher, job["document_id"])["nodes"]
    assert {node["node_id"] for node in final_nodes} == {
        node["node_id"] for node in outline["nodes"]
    }


def test_evidence_tree_pipeline_never_imports_torch_or_docling(tmp_path, monkeypatch):
    db, campus, teacher, course = semantic_setup(tmp_path)
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
    assert completed["analyzer_version"] == "evidence-map-reduce-v5"


def test_rejecting_course_chapter_hides_all_descendants_and_relations(tmp_path):
    db, campus, teacher, course = semantic_setup(tmp_path)
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
