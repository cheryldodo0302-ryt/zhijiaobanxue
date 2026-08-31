from pathlib import Path

from adaptive_ingestion import (
    DEFAULT_BATCH_SIZE,
    BatchParser,
    DocumentInspection,
    PageInspection,
    PageRouter,
    ParseManifest,
)


def test_page_router_keeps_page_type_and_parse_level_independent():
    cover = PageInspection(page_index=0, native_text="高校教材", native_text_chars=4)
    PageRouter.route(cover, total_pages=20, document_kind="native")
    assert cover.page_type == "COVER"
    assert cover.parse_level == "FAST"
    assert cover.include_as_navigation is True
    assert cover.include_as_knowledge is False

    toc = PageInspection(page_index=1, native_text="目录\n第一章 数据模型 1", native_text_chars=14)
    PageRouter.route(toc, total_pages=20, document_kind="native")
    assert toc.page_type == "TOC"
    assert toc.parse_level == "STRUCTURE"
    assert toc.include_as_knowledge is False

    formula_page = PageInspection(
        page_index=2, native_text="定义 E(X)=...", native_text_chars=20,
        image_count=1, formula_candidate_count=4,
    )
    PageRouter.route(formula_page, total_pages=20, document_kind="hybrid")
    assert formula_page.page_type == "CONTENT"
    assert formula_page.parse_level == "DEEP"

    example = PageInspection(page_index=3, native_text="例题：求解随机变量", native_text_chars=10)
    PageRouter.route(example, total_pages=20, document_kind="native")
    assert example.page_type == "EXAMPLE"
    assert example.include_as_knowledge is False


def test_scanned_image_pages_are_ocr_content_not_decorative_visuals():
    page = PageInspection(
        page_index=1, native_text="", native_text_chars=0,
        image_count=1, image_area_ratio=0.85,
    )
    PageRouter.route(page, total_pages=20, document_kind="scanned")
    assert page.page_type == "CONTENT"
    assert page.parse_level == "DEEP"
    assert page.include_as_knowledge is True


def test_manifest_uses_zero_based_page_index_and_configured_batches(tmp_path: Path):
    inspection = DocumentInspection(
        total_pages=81,
        document_kind="native",
        pages=[PageInspection(page_index=index) for index in range(81)],
    )
    manifest = ParseManifest.create("doc_1", inspection, DEFAULT_BATCH_SIZE)
    assert manifest.payload["page_index_base"] == 0
    assert manifest.payload["page_number_base"] == 1
    assert manifest.payload["batches"]["1"]["original_page_end"] == 39
    assert manifest.payload["batches"]["3"]["original_page_start"] == 80
    manifest.set_page(40, status="FAILED", error_message="timeout")
    path = tmp_path / "manifest.json"
    manifest.save(path)
    restored = ParseManifest.load(path)
    assert restored.page(40)["status"] == "FAILED"
    assert restored.page(40)["error_message"] == "timeout"


def test_fast_pages_write_normalized_jsonl_without_calling_mineru(tmp_path: Path):
    class ForbiddenMinerU:
        enabled = True

        def parse(self, *_args, **_kwargs):
            raise AssertionError("FAST pages must not call MinerU")

    page = PageInspection(
        page_index=0, native_text="第三章 概率统计", native_text_chars=8,
        page_type="SECTION", parse_level="FAST",
        include_as_navigation=True, include_as_knowledge=False,
    )
    inspection = DocumentInspection(1, "native", [page])
    result = BatchParser(ForbiddenMinerU()).run(
        tmp_path / "fixture.pdf", "doc_fast", inspection, tmp_path / "ingestion",
    )
    assert result.job_status == "review_required"
    assert result.pages[0]["status"] == "PARSED_OK"
    assert result.pages[0]["page_type"] == "SECTION"
    assert result.pages[0]["include_as_knowledge"] is False
    assert (tmp_path / "ingestion" / "manifest.json").is_file()
    assert (tmp_path / "ingestion" / "normalized" / "blocks.jsonl").read_text(encoding="utf-8").strip()


def test_partial_parse_with_source_blocks_requires_review_not_hard_failure(tmp_path: Path):
    from pypdf import PdfWriter

    class PartialMinerU:
        enabled = True

        def parse(self, *_args, **_kwargs):
            return {
                "_image_paths": {},
                "pdf_info": [{"page_idx": 0, "para_blocks": [{"type": "text", "content": "正文"}]}],
            }

    source = tmp_path / "partial.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    with source.open("wb") as stream:
        writer.write(stream)
    pages = [
        PageInspection(page_index=index, image_count=1, page_type="CONTENT",
                       parse_level="DEEP", include_as_knowledge=True)
        for index in range(2)
    ]
    result = BatchParser(PartialMinerU(), batch_size=2).run(
        source, "doc_partial", DocumentInspection(2, "scanned", pages), tmp_path / "ingestion",
    )
    assert result.blocks
    assert result.job_status == "review_required"
    assert result.manifest.summary()["failed_pages"] == 1
