from pathlib import Path

from auth_service import AuthService
from campus_service import CampusService
from database import LearningDatabase
from ingestion_service import IngestionService
from knowledge_ingestion import KnowledgeBoundaryExtractor, RegionClassifier, StructureBuilder
from teacher_service import TeacherService


def test_region_classifier_inherits_special_region_until_next_heading():
    blocks = [
        {"block_id": "b1", "block_type": "title", "markdown": "# 例题：关系代数", "plain_text": "例题：关系代数"},
        {"block_id": "b2", "block_type": "paragraph", "markdown": "题干和解答", "plain_text": "题干和解答"},
        {"block_id": "b3", "block_type": "title", "markdown": "# 定义：关系", "plain_text": "定义：关系"},
        {"block_id": "b4", "block_type": "paragraph", "markdown": "关系是属性的集合。", "plain_text": "关系是属性的集合。"},
    ]
    result = RegionClassifier().classify(blocks)
    assert [row["region_type"] for row in result] == ["example", "example", "knowledge", "knowledge"]
    assert result[1]["include_as_knowledge"] is False
    assert result[3]["parent_region_block_id"] == "b3"


def test_structure_builder_reports_toc_heading_mismatch():
    blocks = [
        {"block_id": "toc", "block_type": "paragraph", "page_number": 1, "page_type": "TOC",
         "plain_text": "第1章 数据模型 ........ 1\n第2章 查询语言 ........ 8"},
        {"block_id": "h1", "block_type": "title", "page_number": 3, "page_type": "CONTENT",
         "plain_text": "第1章 数据模型", "markdown": "# 第1章 数据模型", "region_type": "knowledge"},
    ]
    result = StructureBuilder().build(blocks)
    assert result["status"] == "warning"
    assert result["warnings"][0]["code"] == "STRUCTURE_WARNING"
    assert result["outline"][0]["title"] == "数据模型"


def test_boundary_is_source_block_first():
    blocks = [
        {"block_id": "chapter", "block_type": "title", "page_number": 1, "block_order": 1,
         "plain_text": "第1章 数据模型", "markdown": "# 第1章 数据模型", "region_type": "knowledge", "include_as_knowledge": True},
        {"block_id": "definition", "block_type": "title", "page_number": 2, "block_order": 2,
         "plain_text": "定义：关系", "markdown": "## 定义：关系", "region_type": "knowledge", "include_as_knowledge": True},
        {"block_id": "body", "block_type": "paragraph", "page_number": 2, "block_order": 3,
         "plain_text": "关系是属性的集合。", "markdown": "关系是属性的集合。", "region_type": "knowledge", "include_as_knowledge": True},
        {"block_id": "exercise", "block_type": "title", "page_number": 3, "block_order": 4,
         "plain_text": "习题", "markdown": "## 习题", "region_type": "exercise", "include_as_knowledge": False},
    ]
    candidates = KnowledgeBoundaryExtractor().extract(blocks, "doc_1")
    assert len(candidates) == 1
    assert candidates[0]["source_block_ids"] == ["definition", "body"]
    assert candidates[0]["markdown_content"] == "## 定义：关系\n\n关系是属性的集合。"


def test_candidate_approval_materializes_approved_source(tmp_path: Path):
    db = LearningDatabase(tmp_path / "candidate.db")
    campus = CampusService(db, tmp_path / "uploads", provider_factory=lambda: None)
    teacher = AuthService(db, tmp_path / "secret").create_user("teacher", "safe-password-123", "teacher")
    course = TeacherService(db, campus).create_course(teacher, "候选知识库")
    ingestion = IngestionService(db, campus)
    job = ingestion.queue_document(
        teacher, course["course_id"], "knowledge.md", "text/markdown",
        "# 第一章 数据模型\n\n关系模型由关系、属性和元组构成。".encode("utf-8"),
    )
    ingestion.process_job(job["job_id"])
    candidates = ingestion.list_knowledge_candidates(teacher, job["document_id"])
    assert candidates and candidates[0]["source_block_ids"]
    assert candidates[0]["source_markdown"] == candidates[0]["markdown_content"]
    assert [block["block_id"] for block in candidates[0]["source_blocks"]] == candidates[0]["source_block_ids"]
    assert candidates[0]["source_locations"]
    approved = ingestion.approve_knowledge_candidate(teacher, candidates[0]["candidate_id"])
    assert approved["review_status"] == "APPROVED"
    block = db.fetch_one(
        "SELECT content_destination,verification_status FROM document_blocks WHERE block_id=?",
        (candidates[0]["source_block_ids"][0],),
    )
    assert block["content_destination"] == "knowledge"
    assert block["verification_status"] == "teacher_verified"
    document = db.fetch_one("SELECT stored_path FROM course_documents WHERE document_id=?", (job["document_id"],))
    artifact = Path(document["stored_path"]).parent / f"{job['document_id']}_ingestion" / "approved" / "knowledge_points.jsonl"
    assert artifact.is_file()
    assert "source_block_ids" in artifact.read_text(encoding="utf-8")
