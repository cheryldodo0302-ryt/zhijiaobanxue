"""Adaptive, resumable PDF ingestion primitives.

This module deliberately stops before AI knowledge extraction.  It keeps the
source document and parser response on disk, emits normalized JSONL blocks, and
returns only source-backed blocks to the existing teacher review pipeline.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from document_ir import formula_anomalies, mineru_to_blocks, normalize_latex


DEFAULT_BATCH_SIZE = 40
PAGE_STATUSES = {
    "PENDING", "PROCESSING", "PARSED_OK", "PARSED_PARTIAL", "TEXT_ONLY", "SUSPECT", "FAILED",
}
PAGE_TYPES = {
    "COVER", "BACK_COVER", "TOC", "SECTION", "CONTENT", "EXAMPLE", "EXERCISE",
    "ENDING", "BLANK", "VISUAL", "UNKNOWN",
}
PARSE_LEVELS = {"SKIP", "FAST", "STRUCTURE", "NORMAL", "DEEP"}


def _safe_json(value: Any) -> Any:
    """Convert parser payload values into JSON-safe data for audit artifacts."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    return str(value)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_safe_json(value), ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(_safe_json(row), ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


@dataclass
class PageInspection:
    page_index: int
    native_text: str = ""
    native_text_chars: int = 0
    text_block_count: int = 0
    image_count: int = 0
    image_area_ratio: float = 0.0
    largest_font_size: float = 0.0
    page_area: float = 0.0
    formula_candidate_count: int = 0
    table_candidate_count: int = 0
    page_type: str = "UNKNOWN"
    parse_level: str = "NORMAL"
    include_as_navigation: bool = False
    include_as_knowledge: bool = True
    extraction_error: str = ""

    @property
    def page_number(self) -> int:
        return self.page_index + 1

    def as_manifest_page(self) -> dict[str, Any]:
        return {
            "page_index": self.page_index,
            "page_number": self.page_number,
            "status": "PENDING",
            "page_type": self.page_type,
            "parse_level": self.parse_level,
            "include_as_navigation": self.include_as_navigation,
            "include_as_knowledge": self.include_as_knowledge,
            "native_text_chars": self.native_text_chars,
            "text_block_count": self.text_block_count,
            "image_count": self.image_count,
            "image_area_ratio": self.image_area_ratio,
            "largest_font_size": self.largest_font_size,
            "page_area": self.page_area,
            "formula_candidate_count": self.formula_candidate_count,
            "table_candidate_count": self.table_candidate_count,
            "error_message": self.extraction_error,
            "block_count": 0,
            "text_chars": 0,
            "equation_count": 0,
            "table_count": 0,
            "parsed_text_chars": 0,
            "validation_issues": [],
        }


@dataclass
class DocumentInspection:
    total_pages: int
    document_kind: str
    pages: list[PageInspection] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_pages": self.total_pages,
            "document_kind": self.document_kind,
            "page_index_base": 0,
            "page_number_base": 1,
            "pages": [page.as_manifest_page() for page in self.pages],
        }


class PageRouter:
    """Route a page by semantic page type and parsing cost independently."""

    @staticmethod
    def page_type(*, page_index: int, total_pages: int, text: str, image_count: int,
                  image_area_ratio: float, native_text_chars: int) -> str:
        compact = re.sub(r"\s+", "", text).lower()
        line_count = sum(bool(line.strip()) for line in text.splitlines())
        if not compact and image_count == 0:
            return "BLANK"
        if page_index == 0 and (native_text_chars < 600 or "封面" in compact):
            return "COVER"
        if page_index == total_pages - 1 and native_text_chars < 120 and image_count:
            return "BACK_COVER"
        if "目录" in compact or "contents" in compact or "tableofcontents" in compact:
            return "TOC"
        if re.search(r"(?:第[一二三四五六七八九十百\d]+章|第[一二三四五六七八九十百\d]+部分|part\s+\d+)", compact):
            if native_text_chars < 260 and line_count <= 8:
                return "SECTION"
        if any(token in compact for token in ("习题", "练习题", "课后题", "思考题", "exercise")):
            return "EXERCISE"
        if any(token in compact for token in ("例题", "例：", "案例", "example", "case")):
            return "EXAMPLE"
        if any(token in compact for token in ("thanks", "thankyou", "q&a", "questionsandanswers")):
            return "ENDING"
        if image_area_ratio >= 0.75 and native_text_chars < 120:
            return "VISUAL"
        return "CONTENT"

    @staticmethod
    def parse_level(*, page_type: str, native_text_chars: int, image_count: int,
                    image_area_ratio: float, formula_candidate_count: int,
                    table_candidate_count: int, document_kind: str,
                    extraction_error: str = "") -> str:
        if page_type in {"BLANK", "BACK_COVER"}:
            return "SKIP"
        if page_type in {"COVER", "SECTION", "ENDING", "VISUAL"}:
            return "FAST"
        if page_type == "TOC":
            return "STRUCTURE"
        if extraction_error or (native_text_chars < 40 and image_count > 0):
            return "DEEP"
        if document_kind == "scanned" or formula_candidate_count >= 3 or table_candidate_count >= 2:
            return "DEEP"
        if document_kind == "hybrid" and native_text_chars < 80:
            return "DEEP"
        return "NORMAL"

    @classmethod
    def route(cls, page: PageInspection, *, total_pages: int, document_kind: str) -> PageInspection:
        if page.extraction_error:
            page.page_type = "UNKNOWN"
            page.parse_level = "DEEP"
            page.include_as_navigation = False
            page.include_as_knowledge = True
            return page
        page.page_type = cls.page_type(
            page_index=page.page_index, total_pages=total_pages, text=page.native_text,
            image_count=page.image_count, image_area_ratio=page.image_area_ratio,
            native_text_chars=page.native_text_chars,
        )
        # A scanned textbook page is commonly represented as one large image.
        # It must still enter OCR and knowledge review; treating every such
        # page as a decorative VISUAL page skips MinerU and produces no IR.
        if document_kind == "scanned" and page.page_type == "VISUAL":
            page.page_type = "CONTENT"
        page.parse_level = cls.parse_level(
            page_type=page.page_type, native_text_chars=page.native_text_chars,
            image_count=page.image_count, image_area_ratio=page.image_area_ratio,
            formula_candidate_count=page.formula_candidate_count,
            table_candidate_count=page.table_candidate_count,
            document_kind=document_kind, extraction_error=page.extraction_error,
        )
        page.include_as_navigation = page.page_type in {"COVER", "TOC", "SECTION"}
        page.include_as_knowledge = page.page_type not in {
            "COVER", "BACK_COVER", "TOC", "SECTION", "ENDING", "BLANK", "VISUAL",
            "EXAMPLE", "EXERCISE",
        }
        return page


class DocumentInspect:
    """Fast, local PDF inspection using native text and page resource hints."""

    @staticmethod
    def _page_metrics(page: Any, page_index: int) -> PageInspection:
        try:
            text = str(page.extract_text() or "")
        except Exception as exc:  # pragma: no cover - depends on malformed PDFs
            text = ""
            error = str(exc)[:500]
        else:
            error = ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        try:
            image_count = len(page.images)
        except Exception:
            image_count = 0
        try:
            box = page.mediabox
            width = float(box.width)
            height = float(box.height)
            page_area = max(width * height, 0.0)
        except Exception:
            page_area = 0.0
        image_area_ratio = min(1.0, 0.85 if image_count and len(text.strip()) < 120 else image_count * 0.25)
        formula_candidates = len(re.findall(r"(?:\\[A-Za-z]+|[=∑∫√^_{}]|\$[^$]+\$)", text))
        table_candidates = sum(1 for line in lines if line.count("|") >= 2 or re.search(r"\s{3,}", line))
        return PageInspection(
            page_index=page_index, native_text=text, native_text_chars=len(text.strip()),
            text_block_count=len(lines), image_count=image_count, image_area_ratio=image_area_ratio,
            page_area=page_area, formula_candidate_count=formula_candidates,
            table_candidate_count=table_candidates, extraction_error=error,
        )

    def inspect_pdf(self, path: Path) -> DocumentInspection:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = [self._page_metrics(page, index) for index, page in enumerate(reader.pages)]
        if not pages:
            raise ValueError("PDF 没有可读取的页面")
        native_pages = sum(page.native_text_chars >= 40 for page in pages)
        image_pages = sum(page.image_count > 0 for page in pages)
        if native_pages / len(pages) >= 0.8:
            document_kind = "native"
        elif image_pages / len(pages) >= 0.65 and native_pages / len(pages) < 0.35:
            document_kind = "scanned"
        else:
            document_kind = "hybrid"
        for page in pages:
            PageRouter.route(page, total_pages=len(pages), document_kind=document_kind)
        return DocumentInspection(len(pages), document_kind, pages)


class ParseManifest:
    """JSON manifest with page and batch checkpoints."""

    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    @classmethod
    def create(cls, document_id: str, inspection: DocumentInspection, batch_size: int) -> "ParseManifest":
        batches = {}
        for number, start in enumerate(range(0, inspection.total_pages, batch_size), 1):
            end = min(inspection.total_pages - 1, start + batch_size - 1)
            batches[str(number)] = {
                "batch_number": number, "original_page_start": start,
                "original_page_end": end, "status": "PENDING", "retry_count": 0,
                "error_message": "", "completed_pages": 0,
            }
        return cls({
            "manifest_version": 1, "document_id": document_id,
            "total_pages": inspection.total_pages, "document_kind": inspection.document_kind,
            "batch_size": batch_size, "page_index_base": 0, "page_number_base": 1,
            "pages": {str(page.page_index): page.as_manifest_page() for page in inspection.pages},
            "batches": batches, "errors": [], "updated_at": "",
        })

    @classmethod
    def load(cls, path: Path) -> "ParseManifest":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def save(self, path: Path) -> None:
        self.payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def page(self, page_index: int) -> dict[str, Any]:
        return self.payload["pages"][str(page_index)]

    def batch(self, batch_number: int) -> dict[str, Any]:
        return self.payload["batches"][str(batch_number)]

    def set_page(self, page_index: int, **updates: Any) -> None:
        updates.setdefault("page_index", page_index)
        if updates.get("status") not in PAGE_STATUSES:
            raise ValueError(f"非法页状态: {updates.get('status')}")
        self.payload["pages"][str(page_index)].update(updates)

    def set_batch(self, batch_number: int, **updates: Any) -> None:
        if updates.get("status") and updates["status"] not in PAGE_STATUSES:
            raise ValueError(f"非法批次状态: {updates.get('status')}")
        self.payload["batches"][str(batch_number)].update(updates)

    def summary(self) -> dict[str, Any]:
        pages = list(self.payload["pages"].values())
        batches = list(self.payload["batches"].values())
        return {
            "total_pages": self.payload["total_pages"],
            "parsed_pages": sum(page.get("status") in {"PARSED_OK", "PARSED_PARTIAL", "TEXT_ONLY", "SUSPECT"} for page in pages),
            "failed_pages": sum(page.get("status") == "FAILED" for page in pages),
            "missing_pages": [page["page_number"] for page in pages if page.get("status") == "FAILED"],
            "suspect_pages": sum(page.get("status") == "SUSPECT" for page in pages),
            "completed_batches": sum(batch.get("status") == "PARSED_OK" for batch in batches),
            "failed_batches": sum(batch.get("status") == "FAILED" for batch in batches),
            "page_types": {page_type: sum(page.get("page_type") == page_type for page in pages)
                           for page_type in sorted(PAGE_TYPES)},
        }


class PageValidator:
    """Detect missing/empty/suspicious pages without deleting low-volume content."""

    @staticmethod
    def validate_page(page: PageInspection, parsed_text_chars: int, block_count: int,
                      previous_native_chars: int | None, next_native_chars: int | None,
                      *, parse_error: str = "") -> list[str]:
        if page.parse_level in {"SKIP", "FAST", "STRUCTURE"}:
            return ["parser_error"] if parse_error else []
        issues: list[str] = []
        if parse_error:
            issues.append("parse_exception")
        if block_count == 0:
            issues.append("missing_structured_blocks")
        if page.native_text_chars >= 40 and parsed_text_chars == 0:
            issues.append("empty_result")
        if page.native_text_chars >= 120 and parsed_text_chars < max(40, int(page.native_text_chars * 0.25)):
            issues.append("native_text_volume_mismatch")
        if (previous_native_chars and next_native_chars and page.native_text_chars < 80
                and previous_native_chars >= 500 and next_native_chars >= 500):
            issues.append("neighbor_text_volume_anomaly")
        return issues


@dataclass
class AdaptiveParseResult:
    inspection: DocumentInspection
    manifest: ParseManifest
    manifest_path: Path
    output_root: Path
    blocks: list[dict[str, Any]]
    pages: list[dict[str, Any]]
    canonical_markdown: str
    job_status: str
    parser_version: str


class BatchParser:
    """Run parser batches and resume only unfinished/failed batches."""

    def __init__(self, mineru: Any, formula: Any = None, *, batch_size: int = DEFAULT_BATCH_SIZE,
                 progress_callback: Callable[[dict[str, Any], int], None] | None = None):
        self.mineru = mineru
        self.formula = formula
        self.batch_size = max(1, int(batch_size))
        self.progress_callback = progress_callback

    def _report_progress(self, manifest: ParseManifest, batch_number: int) -> None:
        if not self.progress_callback:
            return
        try:
            self.progress_callback(manifest.summary(), batch_number)
        except Exception:
            # Progress reporting must never turn a successfully persisted batch
            # into a failed parse.
            return

    @staticmethod
    def _block_text(block: dict[str, Any]) -> str:
        return str(block.get("markdown") or block.get("latex") or block.get("plain_text") or "").strip()

    @staticmethod
    def _native_block(page: PageInspection) -> dict[str, Any] | None:
        text = page.native_text.strip()
        if not text:
            return None
        block_type = "title" if page.page_type in {"COVER", "SECTION", "TOC"} else "paragraph"
        return {
            "block_type": block_type, "markdown": text, "plain_text": text, "latex": "",
            "page_index": page.page_index, "page_number": page.page_number, "bbox": [],
            "confidence": None, "source_method": "native_inspect", "verification_status": "auto_verified",
            "search_aliases": [], "source_image_path": "", "raw": {"source": "native_text_channel"},
        }

    @staticmethod
    def _materialize_batch(source: Path, target: Path, start: int, end: int) -> Path:
        try:
            from pypdf import PdfReader, PdfWriter
            reader = PdfReader(str(source))
        except Exception:
            if start == 0 and end == 0:
                return source
            raise
        writer = PdfWriter()
        for index in range(start, min(end + 1, len(reader.pages))):
            writer.add_page(reader.pages[index])
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as stream:
            writer.write(stream)
        return target

    def _mineru_parse(self, path: Path, *, method: str, asset_dir: Path, raw_dir: Path) -> dict[str, Any]:
        raw_dir.mkdir(parents=True, exist_ok=True)
        try:
            payload = self.mineru.parse(path, method=method, asset_dir=asset_dir, raw_dir=raw_dir)
        except TypeError as exc:
            # Keep compatibility with test doubles and older adapters while the
            # bundled MinerU client supports raw_dir.
            if "raw_dir" not in str(exc):
                raise
            payload = self.mineru.parse(path, method=method, asset_dir=asset_dir)
        _write_json(raw_dir / "response.json", payload)
        markdown = str(payload.get("_markdown") or "")
        if markdown:
            (raw_dir / "output.md").write_text(markdown, encoding="utf-8")
        return payload

    def _verify_formulas(self, blocks: list[dict[str, Any]]) -> None:
        if not self.formula or not getattr(self.formula, "enabled", False):
            return
        for block in blocks:
            source_image = block.get("source_image_path")
            if block.get("block_type") != "formula" or not source_image:
                continue
            secondary = self.formula.recognize(Path(source_image))
            secondary_latex = str(secondary.get("latex") or "")
            block.setdefault("raw", {}).update({
                "formula_secondary_latex": secondary_latex,
                "formula_consistent": normalize_latex(block.get("latex", "")) == normalize_latex(secondary_latex),
                "formula_secondary_engine": secondary.get("engine", "pix2text"),
            })
            if block["raw"]["formula_consistent"] and not formula_anomalies(block.get("latex", "")):
                block["verification_status"] = "auto_verified"

    def run(self, source: Path, document_id: str, inspection: DocumentInspection,
            output_root: Path) -> AdaptiveParseResult:
        output_root.mkdir(parents=True, exist_ok=True)
        for directory in ("batches", "raw", "normalized", "approved", "assets"):
            (output_root / directory).mkdir(parents=True, exist_ok=True)
        manifest_path = output_root / "manifest.json"
        if manifest_path.is_file():
            manifest = ParseManifest.load(manifest_path)
            if (manifest.payload.get("total_pages") != inspection.total_pages
                    or manifest.payload.get("batch_size") != self.batch_size):
                manifest = ParseManifest.create(document_id, inspection, self.batch_size)
        else:
            manifest = ParseManifest.create(document_id, inspection, self.batch_size)
        _write_json(output_root / "raw" / "inspection.json", inspection.as_dict())
        _write_jsonl(
            output_root / "raw" / "native_text.jsonl",
            ({"page_index": page.page_index, "page_number": page.page_number, "text": page.native_text}
             for page in inspection.pages),
        )
        all_blocks: list[dict[str, Any]] = []
        parser_version = "adaptive-v1"
        for batch_number, batch in sorted(
            ((int(key), value) for key, value in manifest.payload["batches"].items()), key=lambda item: item[0]
        ):
            batch_file = output_root / "normalized" / f"batch_{batch_number:03d}.jsonl"
            if batch.get("status") == "PARSED_OK" and batch_file.is_file():
                all_blocks.extend(_read_jsonl(batch_file))
                self._report_progress(manifest, batch_number)
                continue
            start, end = int(batch["original_page_start"]), int(batch["original_page_end"])
            manifest.set_batch(batch_number, status="PROCESSING", error_message="")
            manifest.save(manifest_path)
            batch_blocks: list[dict[str, Any]] = []
            parse_error = ""
            needs_mineru = any(
                inspection.pages[index].parse_level in {"NORMAL", "DEEP"}
                for index in range(start, end + 1)
            )
            try:
                if needs_mineru:
                    batch_path = self._materialize_batch(
                        source, output_root / "batches" / f"batch_{batch_number:03d}" / source.name, start, end,
                    )
                    method = "ocr" if any(inspection.pages[index].parse_level == "DEEP" for index in range(start, end + 1)) else "auto"
                    payload = self._mineru_parse(
                        batch_path, method=method,
                        asset_dir=output_root / "assets" / f"batch_{batch_number:03d}",
                        raw_dir=output_root / "raw" / f"batch_{batch_number:03d}",
                    )
                    parser_version = str(payload.get("_version_name") or parser_version)
                    batch_blocks = mineru_to_blocks(payload)
                    for block in batch_blocks:
                        local_page = int(block.get("page_number") or 1) - 1
                        block["page_index"] = start + local_page
                        block["page_number"] = block["page_index"] + 1
                    self._verify_formulas(batch_blocks)
            except Exception as exc:
                parse_error = str(exc)[:1000]
                manifest.payload.setdefault("errors", []).append({
                    "batch_number": batch_number, "message": parse_error,
                })
            by_page: dict[int, list[dict[str, Any]]] = {}
            for block in batch_blocks:
                by_page.setdefault(int(block["page_index"]), []).append(block)
            for page_index in range(start, end + 1):
                page = inspection.pages[page_index]
                page_blocks = by_page.get(page_index, [])
                if page.parse_level == "SKIP":
                    status, page_error = "PARSED_OK", ""
                elif not page_blocks and page.parse_level in {"FAST", "STRUCTURE"}:
                    fallback = self._native_block(page)
                    page_blocks = [fallback] if fallback else []
                    status, page_error = ("PARSED_OK", "") if fallback else ("TEXT_ONLY", "页面无可提取文字")
                elif not page_blocks and page.native_text.strip():
                    fallback = self._native_block(page)
                    page_blocks = [fallback] if fallback else []
                    status, page_error = "TEXT_ONLY", "结构化解析无结果，保留原生文本通道"
                elif not page_blocks:
                    status, page_error = ("FAILED", parse_error or "页面没有解析结果")
                else:
                    page_error = parse_error
                    previous_chars = inspection.pages[page_index - 1].native_text_chars if page_index else None
                    next_chars = inspection.pages[page_index + 1].native_text_chars if page_index + 1 < inspection.total_pages else None
                    parsed_chars = sum(len(self._block_text(block)) for block in page_blocks)
                    issues = PageValidator.validate_page(
                        page, parsed_chars, len(page_blocks), previous_chars, next_chars,
                        parse_error=parse_error,
                    )
                    status = "SUSPECT" if issues else "PARSED_OK"
                    page_error = "; ".join(issues)
                parsed_chars = sum(len(self._block_text(block)) for block in page_blocks)
                equation_count = sum(block.get("block_type") == "formula" for block in page_blocks)
                table_count = sum(block.get("block_type") == "table" for block in page_blocks)
                for order, block in enumerate(page_blocks, 1):
                    block["block_id"] = f"p{page_index + 1:04d}_b{order:03d}"
                    block["page_index"] = page_index
                    block["page_number"] = page_index + 1
                    block["page_type"] = page.page_type
                    block["parse_level"] = page.parse_level
                    block["include_as_navigation"] = page.include_as_navigation
                    block["include_as_knowledge"] = page.include_as_knowledge
                    block.setdefault("chapter_path", [])
                page_entry = manifest.page(page_index)
                page_entry.update({
                    "status": status, "parse_method": "skip" if page.parse_level == "SKIP" else (
                        "native" if any(block.get("source_method") == "native_inspect" for block in page_blocks) else "mineru"
                    ), "block_count": len(page_blocks), "text_chars": len(page.native_text.strip()),
                    "parsed_text_chars": parsed_chars, "equation_count": equation_count,
                    "table_count": table_count, "error_message": page_error,
                    "validation_issues": [item for item in page_error.split("; ") if item],
                })
                all_blocks.extend(page_blocks)
            batch_status = "FAILED" if any(manifest.page(index)["status"] == "FAILED" for index in range(start, end + 1)) else (
                "PARSED_PARTIAL" if any(manifest.page(index)["status"] in {"SUSPECT", "TEXT_ONLY"} for index in range(start, end + 1)) else "PARSED_OK"
            )
            manifest.set_batch(
                batch_number, status=batch_status,
                completed_pages=sum(manifest.page(index)["status"] != "FAILED" for index in range(start, end + 1)),
                error_message=parse_error,
                retry_count=int(batch.get("retry_count") or 0) + (1 if parse_error else 0),
            )
            _write_jsonl(batch_file, [block for block in all_blocks if start <= int(block.get("page_index", -1)) <= end])
            manifest.save(manifest_path)
            self._report_progress(manifest, batch_number)
        all_blocks = sorted(_read_jsonl_from_batches(output_root / "normalized", manifest), key=lambda block: (int(block.get("page_index", 0)), str(block.get("block_id", ""))))
        _write_jsonl(output_root / "normalized" / "blocks.jsonl", all_blocks)
        canonical = "\n\n".join(self._block_text(block) for block in all_blocks if self._block_text(block))
        (output_root / "normalized" / "document.md").write_text(canonical + ("\n" if canonical else ""), encoding="utf-8")
        chapters = [
            {"page_index": page["page_index"], "page_number": page["page_number"], "page_type": page["page_type"]}
            for page in manifest.payload["pages"].values() if page.get("include_as_navigation")
        ]
        _write_json(output_root / "normalized" / "chapters.json", {"status": "heuristic", "nodes": chapters})
        _write_jsonl(output_root / "approved" / "knowledge_points.jsonl", [])
        summary = manifest.summary()
        manifest.payload["summary"] = summary
        manifest.save(manifest_path)
        if summary["failed_pages"]:
            # Keep usable source blocks reviewable even when a small number of
            # pages failed. A document with no blocks remains a hard failure.
            job_status = "review_required" if all_blocks else "failed"
        elif summary["suspect_pages"] or any(page.get("status") == "TEXT_ONLY" for page in manifest.payload["pages"].values()):
            job_status = "review_required"
        else:
            job_status = "review_required" if all_blocks else "ready"
        return AdaptiveParseResult(
            inspection=inspection, manifest=manifest, manifest_path=manifest_path,
            output_root=output_root, blocks=all_blocks,
            pages=list(manifest.payload["pages"].values()), canonical_markdown=canonical,
            job_status=job_status, parser_version=parser_version,
        )

def _read_jsonl_from_batches(normalized_dir: Path, manifest: ParseManifest) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in sorted(manifest.payload["batches"], key=int):
        rows.extend(_read_jsonl(normalized_dir / f"batch_{int(key):03d}.jsonl"))
    return rows
