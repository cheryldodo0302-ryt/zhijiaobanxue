import json
from types import SimpleNamespace

import pytest

from auth_service import AuthService
from campus_service import CampusService, ValidationError
from course_knowledge_template import Chapter, CourseKnowledgeTree, KnowledgePoint, Section
from database import LearningDatabase
from docling_graph_adapter import DoclingGraphKnowledgeAdapter
from ingestion_service import IngestionService
from teacher_service import TeacherService


def _blocks():
    return [
        {
            "block_id": "block-1", "block_type": "paragraph", "page_number": 2,
            "block_order": 1, "markdown": "关系模型由关系模式和关系实例组成。",
            "plain_text": "关系模型由关系模式和关系实例组成。", "latex": "", "bbox_json": "[1,2,3,4]",
        },
        {
            "block_id": "block-2", "block_type": "paragraph", "page_number": 3,
            "block_order": 2, "markdown": "本段暂时无法可靠分类。",
            "plain_text": "本段暂时无法可靠分类。", "latex": "", "bbox_json": "[]",
        },
    ]


def _tree(evidence: str = "关系模型由关系模式和关系实例组成") -> CourseKnowledgeTree:
    return CourseKnowledgeTree(
        document_title="lesson.md",
        chapters=[Chapter(
            title="第一章 数据模型",
            sections=[Section(
                title="关系模型",
                knowledge_points=[KnowledgePoint(
                    title="关系模型的组成",
                    summary="关系模型包含关系模式和关系实例。",
                    keywords=["关系模型"],
                    source_block_ids=["block-1"],
                    evidence_quotes=[evidence],
                )],
            )],
        )],
        unclassified_block_ids=["block-2"],
    )


def test_adapter_flattens_schema_and_keeps_source_blocks():
    context = SimpleNamespace(
        extracted_models=[_tree()], provenance=SimpleNamespace(resolution="span")
    )
    adapter = DoclingGraphKnowledgeAdapter(
        lambda: None,
        settings={"backend": "docling_graph", "contract": "auto"},
        runner=lambda _config: context,
    )
    result = adapter.analyze(_blocks(), document_title="lesson.md")
    point = result["knowledge_points"][0]
    assert point["chapter"] == "第一章 数据模型"
    assert point["section"] == "关系模型"
    assert point["block_ids"] == ["block-1"]
    assert {row["block_id"]: row["destination"] for row in result["classifications"]} == {
        "block-1": "knowledge", "block-2": "unclassified",
    }
    assert result["provenance_resolution"] == "span"


def test_annotation_keeps_complete_canonical_markdown_and_injects_block_markers():
    canonical = "# 第一章\n\n关系模型由关系模式和关系实例组成。\n\n本段暂时无法可靠分类。"
    annotated = DoclingGraphKnowledgeAdapter._annotated_markdown(
        _blocks(), "lesson.md", canonical_markdown=canonical
    )
    assert "# 第一章" in annotated
    assert "关系模型由关系模式和关系实例组成。" in annotated
    assert "本段暂时无法可靠分类。" in annotated
    assert "[ZHIJIAO_BLOCK:block-1|PAGE:2]" in annotated
    assert "[ZHIJIAO_BLOCK:block-2|PAGE:3]" in annotated


def test_adapter_rejects_evidence_that_is_not_in_source():
    adapter = DoclingGraphKnowledgeAdapter(
        lambda: None,
        settings={"backend": "docling_graph", "contract": "auto"},
        runner=lambda _config: SimpleNamespace(extracted_models=[_tree("原文不存在的证据")]),
    )
    with pytest.raises(ValidationError, match="证据不能在所引用原文块中逐字定位"):
        adapter.analyze(_blocks(), document_title="lesson.md")


class CourseReducerProvider:
    def generate(self, _system, prompt):
        payload = json.loads(prompt)
        if "blocks" in payload:
            source = payload["blocks"][0]
            return json.dumps({
                "classifications": [{
                    "block_id": row["block_id"],
                    "destination": "knowledge" if index == 0 else "unclassified",
                    "semantic_role": "source_markdown" if index == 0 else "teacher_review",
                    "confidence": .9, "reason": "测试",
                } for index, row in enumerate(payload["blocks"])],
                "knowledge_points": [{
                    "point_key": "point-1", "chapter": "第一章 数据模型",
                    "section": "关系模型", "title": "关系模型的组成",
                    "keywords": ["关系模型"], "block_ids": [source["block_id"]],
                    "evidence_quotes": [source["content"][:20]],
                }],
            }, ensure_ascii=False)
        if "candidates" in payload:
            source = payload["candidates"][0]
            return json.dumps({"points": [{
                "point_key": "reduced-1", "chapter": source["chapter"],
                "section": source["section"], "title": source["title"],
                "keywords": source["keywords"],
                "source_candidate_ids": [source["candidate_id"]],
            }]}, ensure_ascii=False)
        points = [{
            "course_key": f"course-{index}",
            "chapter": row["chapter"], "section": row["section"],
            "title": row["title"], "keywords": row["keywords"],
            "source_node_ids": [row["source_node_id"]],
        } for index, row in enumerate(payload["source_points"])]
        return json.dumps({"points": points, "relations": []}, ensure_ascii=False)


class FakeDoclingGraph:
    enabled = True

    def __init__(self):
        self.document_title = ""

    def analyze(self, blocks, *, document_title, canonical_markdown="", on_call=None):
        self.document_title = document_title
        assert "关系模型" in canonical_markdown
        if on_call:
            on_call()
        source = next(row for row in blocks if "关系模型" in row["markdown"])
        return {
            "classifications": [{
                "block_id": row["block_id"], "destination": "knowledge",
                "semantic_role": "source_markdown", "reason": "测试",
            } for row in blocks],
            "knowledge_points": [{
                "point_key": "point-1", "chapter": "第一章 数据模型", "section": "关系模型",
                "title": "关系模型的组成", "summary": "由模式和实例组成", "keywords": ["关系模型"],
                "block_ids": [source["block_id"]], "evidence_quotes": ["关系模型"],
            }],
            "analyzer_version": "docling-graph-test", "prompt_version": "teacher-course-tree-v2",
            "provenance_resolution": "block-marker",
        }


def test_shared_course_pipeline_uses_native_evidence_tree_without_docling(tmp_path):
    db = LearningDatabase(tmp_path / "docling.db")
    campus = CampusService(db, tmp_path / "uploads", provider_factory=CourseReducerProvider)
    teacher = AuthService(db, tmp_path / "secret").create_user(
        "teacher", "safe-password-123", "teacher"
    )
    course = TeacherService(db, campus).create_course(teacher, "数据库")
    service = IngestionService(db, campus)
    upload = service.queue_document(
        teacher, course["course_id"], "lesson.md", "text/markdown",
        "关系模型由关系模式和关系实例组成。\n未分类附注。".encode(),
    )
    service.process_job(upload["job_id"])
    analysis = db.fetch_one(
        "SELECT * FROM semantic_analysis_jobs WHERE document_id=?", (upload["document_id"],)
    )
    service.process_semantic_analysis(analysis["analysis_job_id"])
    completed = service.get_analysis_job(teacher, analysis["analysis_job_id"])
    assert completed["status"] == "review_required", completed["error_message"]
    assert completed["analyzer_version"] == "evidence-map-reduce-v4"
    assert json.loads(completed["result_json"])["extractor"] == "evidence-map-reduce"
    points = db.fetch_all(
        "SELECT * FROM knowledge_nodes WHERE document_id=? AND node_type='knowledge_point'",
        (upload["document_id"],),
    )
    assert len(points) == 1 and points[0]["title"] == "关系模型的组成"
    source = db.fetch_one(
        "SELECT * FROM knowledge_node_sources WHERE node_id=?", (points[0]["node_id"],)
    )
    assert source and source["document_id"] == upload["document_id"]
