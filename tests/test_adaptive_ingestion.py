from pathlib import Path

from adaptive_ingestion import (
    DEFAULT_BATCH_SIZE,
    BatchParser,
    DocumentInspection,
    PageInspection,
    PageRouter,
    ParseManifest,
    recover_textual_formula_blocks,
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


def test_chinese_word_equation_uses_native_text_instead_of_formula_hallucination():
    blocks = [
        {"block_type": "paragraph", "markdown": "可简单表示为：", "plain_text": "可简单表示为：", "latex": ""},
        {
            "block_type": "formula",
            "markdown": r"17-5=3 \times 5=3 \times 5",
            "plain_text": r"17-5=3 \times 5=3 \times 5",
            "latex": r"17-5=3 \times 5=3 \times 5",
            "raw": {},
        },
    ]

    recover_textual_formula_blocks(
        blocks,
        "可简单地用下列式子表示信息、数据与数据处理的关系：\n信息＝数据＋数据处理",
    )

    assert blocks[1]["block_type"] == "paragraph"
    assert blocks[1]["plain_text"] == "信息＝数据＋数据处理"
    assert blocks[1]["latex"] == ""
    assert blocks[1]["raw"]["formula_ocr_original"].startswith("17-5")


def test_mixed_batch_does_not_force_ocr_on_native_pages(tmp_path: Path):
    from pypdf import PdfWriter

    class RecordingMinerU:
        enabled = True

        def __init__(self):
            self.methods = []

        def parse(self, _path, *, method, **_kwargs):
            self.methods.append(method)
            return {
                "_image_paths": {},
                "pdf_info": [
                    {"page_idx": 0, "para_blocks": [{"type": "text", "content": "原生文字页"}]},
                    {"page_idx": 1, "para_blocks": [{"type": "text", "content": "OCR 页"}]},
                ],
            }

    source = tmp_path / "mixed.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    with source.open("wb") as stream:
        writer.write(stream)
    pages = [
        PageInspection(page_index=0, native_text="原生文字页", native_text_chars=120,
                       page_type="CONTENT", parse_level="NORMAL"),
        PageInspection(page_index=1, image_count=1, page_type="CONTENT", parse_level="DEEP"),
    ]
    mineru = RecordingMinerU()

    BatchParser(mineru, batch_size=2).run(
        source, "doc_mixed", DocumentInspection(2, "hybrid", pages), tmp_path / "ingestion",
    )

    assert mineru.methods == ["auto"]


def test_scanned_chinese_word_equation_gets_general_ocr_second_pass(tmp_path: Path):
    image = tmp_path / "formula.jpg"
    image.write_bytes(b"image")

    class TextOcrMinerU:
        enabled = True

        def parse(self, path, *, formula_enable=True, **_kwargs):
            assert Path(path) == image
            assert formula_enable is False
            return {
                "_image_paths": {},
                "pdf_info": [{"page_idx": 0, "para_blocks": [
                    {"type": "text", "content": "信息＝数据＋数据处理"},
                ]}],
            }

    block = {
        "block_type": "formula",
        "markdown": r"1 7 - 5 = 3 \times 5 = 3 \times 5 = 5 \times 5",
        "plain_text": r"1 7 - 5 = 3 \times 5 = 3 \times 5 = 5 \times 5",
        "latex": r"1 7 - 5 = 3 \times 5 = 3 \times 5 = 5 \times 5",
        "source_image_path": str(image),
        "raw": {},
    }

    BatchParser(TextOcrMinerU())._verify_formulas([block])

    assert block["block_type"] == "paragraph"
    assert block["plain_text"] == "信息＝数据＋数据处理"
    assert block["raw"]["textual_formula_recovered"] is True


def test_old_manifest_is_invalidated_when_normalization_changes(tmp_path: Path):
    from pypdf import PdfWriter

    class MinerU:
        enabled = True

        def __init__(self):
            self.calls = 0

        def parse(self, *_args, **_kwargs):
            self.calls += 1
            return {"_image_paths": {}, "pdf_info": [{"page_idx": 0, "para_blocks": [
                {"type": "text", "content": "重新标准化"},
            ]}]}

    source = tmp_path / "versioned.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with source.open("wb") as stream:
        writer.write(stream)
    output = tmp_path / "ingestion"
    output.mkdir()
    (output / "manifest.json").write_text(
        '{"manifest_version":1,"total_pages":1,"batch_size":40,"pages":{},"batches":{}}',
        encoding="utf-8",
    )
    page = PageInspection(page_index=0, image_count=1, page_type="CONTENT", parse_level="DEEP")
    mineru = MinerU()

    result = BatchParser(mineru).run(
        source, "doc_versioned", DocumentInspection(1, "scanned", [page]), output,
    )

    assert mineru.calls == 1
    assert result.blocks[0]["plain_text"] == "重新标准化"


def test_optional_formula_reviewer_outage_does_not_fail_batch(tmp_path: Path):
    image = tmp_path / "equation.jpg"
    image.write_bytes(b"image")

    class MinerU:
        enabled = True

    class OfflineFormula:
        enabled = True

        def recognize(self, _path):
            raise RuntimeError("formula worker unavailable")

    block = {
        "block_type": "formula", "markdown": "E=mc^2", "plain_text": "E=mc^2",
        "latex": "E=mc^2", "source_image_path": str(image), "raw": {},
        "verification_status": "review_required",
    }

    BatchParser(MinerU(), OfflineFormula())._verify_formulas([block])

    assert block["latex"] == "E=mc^2"
    assert block["verification_status"] == "review_required"
    assert "unavailable" in block["raw"]["formula_secondary_error"]
