from __future__ import annotations

import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, BinaryIO

from campus_service import (
    ALLOWED_FILES,
    MAX_UPLOAD_BYTES,
    CampusService,
    NotFound,
    PermissionDenied,
    ValidationError,
    _safe_name,
    parse_document,
)
from database import LearningDatabase
from config import (
    get_document_ingestion_settings,
    get_knowledge_extractor_settings,
    validate_ai_base_url,
)
from adaptive_ingestion import (
    BatchParser,
    DEFAULT_BATCH_SIZE,
    DocumentInspect,
    DocumentInspection,
    PageInspection,
)
from document_ir import formula_anomalies, mineru_to_blocks, normalize_latex, search_aliases
from formula_client import Pix2TextClient
from job_secret_store import decrypt_job_secret, encrypt_job_secret
from knowledge_ingestion import (
    KnowledgeBoundaryExtractor,
    PptFastInspector,
    RegionClassifier,
    StructureBuilder,
    is_outline_field_heading,
    is_outline_unit_heading,
)
from llm_provider import GeminiProvider, OllamaProvider, QwenProvider
from mineru_client import MinerUClient
from semantic_knowledge_service import SemanticKnowledgeService
from security_utils import UnsafeUpload, validate_document_path


MATERIAL_TYPES = {
    "syllabus", "lesson_plan", "slides", "textbook", "experiment",
    "question_bank", "knowledge_graph", "teaching_schedule", "other",
}
MATERIAL_LABELS = {
    "slides": "课件", "textbook": "教材", "syllabus": "教学大纲",
    "lesson_plan": "教案", "experiment": "实验资料", "question_bank": "题库",
    "knowledge_graph": "知识图谱", "teaching_schedule": "教学进度", "other": "其他",
}
MATERIAL_ORDER = tuple(MATERIAL_LABELS)
TEACHING_CATEGORY_LABELS = {
    "course_profile": "课程基本信息",
    "objectives": "培养与学习目标",
    "teaching_design": "教学与学习设计",
    "assessment": "考核与成绩评定",
}


class IngestionService:
    """Persistent orchestration boundary for native and external parsers."""

    def __init__(self, db: LearningDatabase, campus: CampusService):
        self.db = db
        self.campus = campus
        self.mineru = MinerUClient()
        self.formula = Pix2TextClient()
        self.semantic = SemanticKnowledgeService(campus.provider_factory)
        self.region_classifier = RegionClassifier()
        self.structure_builder = StructureBuilder()
        self.boundary_extractor = KnowledgeBoundaryExtractor()
        self.ppt_inspector = PptFastInspector()

    @staticmethod
    def _require_teacher(actor: dict[str, Any]) -> str:
        if actor.get("role") != "teacher":
            raise PermissionDenied("仅教师可以管理自有智能服务配置")
        return str(actor["user_id"])

    def get_teacher_ai_settings(self, actor: dict[str, Any]) -> dict[str, Any]:
        teacher_id = self._require_teacher(actor)
        row = self.db.fetch_one(
            "SELECT * FROM teacher_ai_settings WHERE teacher_id=?", (teacher_id,)
        )
        if not row:
            return {
                "provider": "openai_compatible", "base_url": "", "model": "",
                "has_api_key": False, "verification_status": "untested",
                "verification_message": "尚未保存教师自有 API 配置", "verified_at": None,
            }
        return {
            "provider": row["provider"], "base_url": row["base_url"], "model": row["model"],
            "has_api_key": bool(str(row.get("api_key_encrypted") or "")),
            "verification_status": row["verification_status"],
            "verification_message": row["verification_message"], "verified_at": row["verified_at"],
        }

    @staticmethod
    def _validate_custom_ai_fields(provider: str, base_url: str, model: str) -> None:
        if provider not in {"openai_compatible", "gemini", "ollama"}:
            raise ValidationError("自有 AI 协议只支持 OpenAI 兼容接口、Google Gemini 或 Ollama")
        try:
            validate_ai_base_url(base_url, allow_private=provider == "ollama")
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if not model:
            raise ValidationError("必须填写模型名称")

    def save_teacher_ai_settings(self, actor: dict[str, Any], *, provider: str,
                                 base_url: str, model: str, api_key: str = "") -> dict[str, Any]:
        teacher_id = self._require_teacher(actor)
        provider = str(provider or "openai_compatible").strip()
        base_url = str(base_url or "").strip().rstrip("/")
        model = str(model or "").strip()
        api_key = str(api_key or "").strip()
        self._validate_custom_ai_fields(provider, base_url, model)
        existing = self.db.fetch_one(
            "SELECT * FROM teacher_ai_settings WHERE teacher_id=?", (teacher_id,)
        )
        encrypted = (
            "" if provider == "ollama" else
            encrypt_job_secret(api_key) if api_key else
            str((existing or {}).get("api_key_encrypted") or "")
        )
        if provider != "ollama" and not encrypted:
            raise ValidationError("首次保存时必须填写 API Key")
        changed = not existing or any((
            str((existing or {}).get("provider") or "") != provider,
            str((existing or {}).get("base_url") or "") != base_url,
            str((existing or {}).get("model") or "") != model,
            bool(api_key),
        ))
        status = "untested" if changed else str(existing.get("verification_status") or "untested")
        message = "配置已加密保存，尚未测试连接" if changed else str(existing.get("verification_message") or "")
        self.db.execute(
            """INSERT INTO teacher_ai_settings(
                   teacher_id,provider,base_url,model,api_key_encrypted,verification_status,
                   verification_message,verified_at
               ) VALUES(?,?,?,?,?,?,?,NULL)
               ON CONFLICT(teacher_id) DO UPDATE SET
                   provider=excluded.provider,base_url=excluded.base_url,model=excluded.model,
                   api_key_encrypted=excluded.api_key_encrypted,
                   verification_status=excluded.verification_status,
                   verification_message=excluded.verification_message,
                   verified_at=CASE WHEN excluded.verification_status='untested' THEN NULL
                                    ELSE teacher_ai_settings.verified_at END,
                   updated_at=CURRENT_TIMESTAMP""",
            (teacher_id, provider, base_url, model, encrypted, status, message),
        )
        return self.get_teacher_ai_settings(actor)

    @staticmethod
    def _custom_ai_provider(provider: str, api_key: str, base_url: str, model: str,
                            timeout: int = 20) -> Any:
        if provider == "ollama":
            return OllamaProvider(base_url, model, timeout=timeout)
        provider_class = GeminiProvider if provider == "gemini" else QwenProvider
        return provider_class(api_key, base_url, model, timeout=timeout)

    def test_teacher_ai_settings(self, actor: dict[str, Any]) -> dict[str, Any]:
        teacher_id = self._require_teacher(actor)
        row = self.db.fetch_one(
            "SELECT * FROM teacher_ai_settings WHERE teacher_id=?", (teacher_id,)
        )
        if not row:
            raise ValidationError("请先保存教师自有 API 配置")
        api_key = decrypt_job_secret(str(row.get("api_key_encrypted") or ""))
        if row["provider"] != "ollama" and not api_key:
            raise ValidationError("已保存配置中没有可用 API Key，请重新保存")
        try:
            provider = self._custom_ai_provider(
                str(row["provider"]), api_key, str(row["base_url"]), str(row["model"])
            )
            result = provider.generate_json(
                "你正在执行连接测试。只返回 JSON，不要解释。",
                '{"task":"connection_test","required_output":{"ok":true}}',
            )
            if not isinstance(result, dict):
                raise RuntimeError("接口已响应，但未返回 JSON 对象")
            status, message = "connected", "连接成功，模型已返回有效 JSON"
        except Exception as exc:
            status, message = "failed", str(exc)[:800]
        self.db.execute(
            """UPDATE teacher_ai_settings SET verification_status=?,verification_message=?,
               verified_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE teacher_id=?""",
            (status, message, teacher_id),
        )
        result = self.get_teacher_ai_settings(actor)
        if status == "failed":
            raise ValidationError(f"连接测试失败：{message}")
        return result

    def _resolve_ai_settings(self, actor: dict[str, Any],
                             ai_settings: dict[str, Any] | None) -> dict[str, str]:
        settings = ai_settings or {}
        use_saved = bool(settings.get("use_saved"))
        saved = None
        if use_saved:
            teacher_id = self._require_teacher(actor)
            saved = self.db.fetch_one(
                "SELECT * FROM teacher_ai_settings WHERE teacher_id=?", (teacher_id,)
            )
            if not saved:
                raise ValidationError("尚未保存教师自有 API 配置")
        custom_key = str(settings.get("api_key") or "").strip()
        if not custom_key and saved:
            custom_key = decrypt_job_secret(str(saved.get("api_key_encrypted") or ""))
        custom_base = str(settings.get("base_url") or (saved or {}).get("base_url") or "").strip().rstrip("/")
        custom_model = str(settings.get("model") or (saved or {}).get("model") or "").strip()
        custom_provider = str(
            settings.get("provider") or (saved or {}).get("provider") or "openai_compatible"
        ).strip()
        if custom_key or custom_base or custom_model or use_saved:
            self._validate_custom_ai_fields(custom_provider, custom_base, custom_model)
            if custom_provider != "ollama" and not custom_key:
                raise ValidationError("非 Ollama 自有接口必须填写 API Key")
        return {
            "api_key": custom_key, "base_url": custom_base,
            "model": custom_model, "provider": custom_provider,
        }

    def _adaptive_output_root(self, job: dict[str, Any]) -> Path:
        return Path(job["stored_path"]).parent / f"{job['document_id']}_ingestion"

    @staticmethod
    def _fallback_pdf_inspection() -> DocumentInspection:
        """Keep old parser test doubles and damaged upload diagnostics usable."""
        page = PageInspection(
            page_index=0, native_text="", native_text_chars=0, page_type="CONTENT",
            parse_level="NORMAL", include_as_knowledge=True,
        )
        return DocumentInspection(total_pages=1, document_kind="unknown", pages=[page])

    def _process_pdf_adaptive(self, job: dict[str, Any], path: Path) -> Any:
        try:
            inspection = DocumentInspect().inspect_pdf(path)
        except Exception as exc:
            # A malformed PDF still gets a manifest and a per-batch error. This
            # also preserves the existing adapter contract for parser doubles.
            inspection = self._fallback_pdf_inspection()
            inspection.pages[0].extraction_error = str(exc)[:500]
        batch_size = int(job.get("batch_size") or get_document_ingestion_settings().get("batch_size") or DEFAULT_BATCH_SIZE)
        output_root = self._adaptive_output_root(job)
        self.db.execute(
            """UPDATE ingestion_jobs SET pipeline_stage='INSPECTING',total_pages=?,batch_size=?,
               manifest_path=?,updated_at=CURRENT_TIMESTAMP WHERE job_id=?""",
            (inspection.total_pages, batch_size, str(output_root / "manifest.json"), job["job_id"]),
        )

        def report_progress(summary: dict[str, Any], _batch_number: int) -> None:
            total = max(1, int(summary.get("total_pages") or inspection.total_pages))
            parsed = int(summary.get("parsed_pages") or 0)
            self.db.execute(
                """UPDATE ingestion_jobs SET status='running',pipeline_stage='PARSING',progress=?,
                   total_pages=?,completed_pages=?,failed_pages=?,updated_at=CURRENT_TIMESTAMP WHERE job_id=?""",
                (min(99.0, parsed * 100.0 / total), total, parsed,
                 int(summary.get("failed_pages") or 0), job["job_id"]),
            )

        parser = BatchParser(
            self.mineru, self.formula, batch_size=batch_size, progress_callback=report_progress,
        )
        return parser.run(path, job["document_id"], inspection, output_root)

    def _annotate_source_blocks(
        self, blocks: list[dict[str, Any]], pages: list[dict[str, Any]] | None = None,
        document_id: str = "",
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Add deterministic region/outline metadata without changing source text."""
        page_rows = {int(row.get("page_number") or 0): row for row in (pages or [])}
        for index, block in enumerate(blocks, 1):
            block.setdefault("block_id", f"{document_id or 'source'}_b{index:06d}")
            page_number = int(block.get("page_number") or 1)
            page = page_rows.get(page_number, {})
            block.setdefault("page_index", page_number - 1)
            block.setdefault("page_type", page.get("page_type", "CONTENT"))
            block.setdefault("parse_level", page.get("parse_level", "NORMAL"))
            block.setdefault("include_as_navigation", bool(page.get("include_as_navigation", False)))
            block.setdefault("include_as_knowledge", bool(page.get("include_as_knowledge", True)))
            block.setdefault("chapter_path", [])
        annotated = self.region_classifier.classify(blocks)
        structure = self.structure_builder.build(annotated, pages or [])
        chapter_paths = structure.get("block_chapter_paths") or {}
        for block in annotated:
            block["chapter_path"] = chapter_paths.get(str(block.get("block_id") or ""), [])
        return annotated, structure

    @staticmethod
    def _write_normalized_structure(output_root: Path, blocks: list[dict[str, Any]], structure: dict[str, Any]) -> None:
        normalized = output_root / "normalized"
        normalized.mkdir(parents=True, exist_ok=True)
        with (normalized / "blocks.jsonl").open("w", encoding="utf-8") as stream:
            for block in blocks:
                stream.write(json.dumps(block, ensure_ascii=False) + "\n")
        (normalized / "document.md").write_text(
            "\n\n".join(str(block.get("markdown") or block.get("latex") or block.get("plain_text") or "").strip()
                         for block in blocks if str(block.get("markdown") or block.get("latex") or block.get("plain_text") or "").strip())
            + "\n",
            encoding="utf-8",
        )
        (normalized / "chapters.json").write_text(
            json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _persist_document_structure(self, document_id: str, structure: dict[str, Any]) -> None:
        self.db.execute(
            """INSERT INTO document_structures(document_id,outline_json,toc_entries_json,warnings_json,status,updated_at)
               VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(document_id) DO UPDATE SET outline_json=excluded.outline_json,
                   toc_entries_json=excluded.toc_entries_json,warnings_json=excluded.warnings_json,
                   status=excluded.status,updated_at=CURRENT_TIMESTAMP""",
            (document_id, json.dumps(structure.get("outline") or [], ensure_ascii=False),
             json.dumps(structure.get("toc_entries") or [], ensure_ascii=False),
             json.dumps(structure.get("warnings") or [], ensure_ascii=False),
             str(structure.get("status") or "ok")),
        )

    def _rebuild_knowledge_candidates(self, document_id: str, course_id: str) -> None:
        rows = self.db.fetch_all(
            "SELECT * FROM document_blocks WHERE document_id=? ORDER BY page_index,block_order",
            (document_id,),
        )
        blocks: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["bbox"] = json.loads(item.pop("bbox_json") or "[]")
            except json.JSONDecodeError:
                item["bbox"] = []
            try:
                item["chapter_path"] = json.loads(item.get("chapter_path_json") or "[]")
            except json.JSONDecodeError:
                item["chapter_path"] = []
            blocks.append(item)
        candidates = self.boundary_extractor.extract(blocks, document_id)
        with self.db.connect() as conn:
            conn.execute("UPDATE document_blocks SET knowledge_candidate=0 WHERE document_id=?", (document_id,))
            candidate_ids = [str(candidate["candidate_id"]) for candidate in candidates]
            stale_params: tuple[Any, ...] = (document_id,)
            stale_sql = (
                "DELETE FROM knowledge_candidates WHERE document_id=? "
                "AND review_status IN ('PENDING','NEEDS_REVIEW')"
            )
            if candidate_ids:
                stale_sql += " AND candidate_id NOT IN ({})".format(
                    ",".join("?" for _ in candidate_ids)
                )
                stale_params += tuple(candidate_ids)
            conn.execute(stale_sql, stale_params)
            for candidate in candidates:
                existing = conn.execute(
                    "SELECT review_status FROM knowledge_candidates WHERE candidate_id=?", (candidate["candidate_id"],)
                ).fetchone()
                conn.execute("DELETE FROM knowledge_candidate_blocks WHERE candidate_id=?", (candidate["candidate_id"],))
                for block_id in candidate["source_block_ids"]:
                    conn.execute("UPDATE document_blocks SET knowledge_candidate=1 WHERE block_id=?", (block_id,))
                if existing and str(existing[0]) in {"APPROVED", "MODIFIED", "REJECTED"}:
                    if str(existing[0]) in {"APPROVED", "MODIFIED"}:
                        conn.execute(
                            """UPDATE document_blocks SET content_destination='knowledge',
                               verification_status='teacher_verified',include_as_knowledge=1,
                               updated_at=CURRENT_TIMESTAMP WHERE block_id IN ({})""".format(
                                ",".join("?" for _ in candidate["source_block_ids"]) or "NULL"
                            ), tuple(candidate["source_block_ids"]),
                        )
                    conn.executemany(
                        "INSERT INTO knowledge_candidate_blocks(candidate_id,block_id,sort_order) VALUES(?,?,?)",
                        [(candidate["candidate_id"], block_id, order)
                         for order, block_id in enumerate(candidate["source_block_ids"])],
                    )
                    continue
                conn.execute("DELETE FROM knowledge_candidates WHERE candidate_id=?", (candidate["candidate_id"],))
                conn.execute(
                    """INSERT INTO knowledge_candidates(
                           candidate_id,course_id,document_id,title,knowledge_type,source_block_ids_json,
                           page_start,page_end,bbox_json,markdown_content,confidence,region_type,chapter_path_json,review_status)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?, 'PENDING')""",
                    (candidate["candidate_id"], course_id, document_id, candidate["title"],
                     candidate["knowledge_type"], json.dumps(candidate["source_block_ids"], ensure_ascii=False),
                     candidate["page_start"], candidate["page_end"], json.dumps(candidate["bbox"], ensure_ascii=False),
                     candidate["markdown_content"], candidate["confidence"], candidate["region_type"],
                     json.dumps(candidate.get("chapter_path") or [], ensure_ascii=False)),
                )
                conn.executemany(
                    "INSERT INTO knowledge_candidate_blocks(candidate_id,block_id,sort_order) VALUES(?,?,?)",
                    [(candidate["candidate_id"], block_id, order)
                     for order, block_id in enumerate(candidate["source_block_ids"])],
                )

    def _inspect_ppt_fast(self, job: dict[str, Any], path: Path) -> list[dict[str, Any]]:
        root = self._adaptive_output_root(job)
        raw = root / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        try:
            slides = self.ppt_inspector.inspect(path)
        except Exception as exc:
            (raw / "ppt_inspection.json").write_text(
                json.dumps({"status": "failed", "error": str(exc)[:1000], "slides": []}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return []
        (raw / "ppt_inspection.json").write_text(
            json.dumps({"status": "ok", "slide_count": len(slides), "slides": slides}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (root / "normalized").mkdir(parents=True, exist_ok=True)
        (root / "normalized" / "slides.json").write_text(
            json.dumps({"status": "ok", "slide_count": len(slides), "slides": slides}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with self.db.connect() as conn:
            conn.execute("DELETE FROM presentation_slides WHERE document_id=?", (job["document_id"],))
            conn.executemany(
                """INSERT INTO presentation_slides(
                       document_id,slide_index,slide_type,parse_level,title,shape_count,text_count,
                       picture_count,reading_order_json,shapes_json,layout_kind,regions_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(job["document_id"], int(slide["slide_index"]), slide["slide_type"], slide["parse_level"],
                  slide.get("title", ""), int(slide.get("shape_count") or 0), int(slide.get("text_count") or 0),
                  int(slide.get("picture_count") or 0), json.dumps(slide.get("reading_order") or []),
                  json.dumps(slide.get("shapes") or [], ensure_ascii=False), slide.get("layout_kind", "single_column"),
                  json.dumps(slide.get("regions") or [], ensure_ascii=False)) for slide in slides],
            )
        return slides

    @staticmethod
    def _ppt_title_hint(title: str) -> dict[str, Any]:
        """Return a conservative local level hint; AI resolves ambiguous cases."""
        cleaned = re.sub(r"^\s*#{1,6}\s*", "", str(title or "")).strip()
        chapter = re.match(r"^第\s*[一二三四五六七八九十百0-9]+\s*[章篇部]", cleaned)
        if chapter:
            return {"number": chapter.group(0), "level": 1, "ambiguous": False}
        section = re.match(r"^第\s*[一二三四五六七八九十百0-9]+\s*节", cleaned)
        if section:
            return {"number": section.group(0), "level": 2, "ambiguous": False}
        dotted = re.match(r"^(\d+(?:\.\d+)+)(?!\d)", cleaned)
        if dotted:
            number = dotted.group(1)
            return {"number": number, "level": min(3, number.count(".") + 1), "ambiguous": False}
        single = re.match(r"^(\d+)[.)、]?(?:\s+|$)", cleaned)
        if single:
            # "3" may mean chapter 3, a third bullet, or a descendant of 1.3.
            return {"number": single.group(1), "level": 3, "ambiguous": True}
        return {"number": "", "level": 2, "ambiguous": False}

    def _ppt_chunks_from_slides(self, slides: list[dict[str, Any]]) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        active_title = ""
        for slide in slides:
            slide_number = int(slide.get("slide_number") or int(slide.get("slide_index") or 0) + 1)
            slide_type = str(slide.get("slide_type") or "SIMPLE_CONTENT")
            detected_title = re.sub(r"\s+", " ", str(slide.get("title") or "")).strip()
            inherited = False
            if slide_type in {"COVER", "ENDING"}:
                active_title = ""
            elif detected_title:
                active_title = detected_title
            elif active_title:
                detected_title = active_title
                inherited = True
            title = detected_title or f"第 {slide_number} 页"
            hint = self._ppt_title_hint(title)
            shape_map = {
                int(shape.get("shape_id") or 0): shape
                for shape in slide.get("shapes") or [] if isinstance(shape, dict)
            }
            body_parts = [
                str(shape_map[shape_id].get("text") or "").strip()
                for shape_id in slide.get("reading_order") or []
                if shape_id in shape_map and not bool(shape_map[shape_id].get("is_title"))
                and not bool(shape_map[shape_id].get("is_boilerplate"))
                and str(shape_map[shape_id].get("text") or "").strip()
            ]
            body = "\n".join(body_parts).strip()
            content_parts = [body[start:start + 1100].strip() for start in range(0, len(body), 900)] if body else [""]
            for part_index, content in enumerate(content_parts):
                chunks.append({
                    "section": title, "page_number": slide_number, "content": content,
                    "heading_level": 0 if inherited else int(hint["level"]), "heading_path": [title],
                    "source_kind": "pptx", "ppt_slide_index": int(slide.get("slide_index") or slide_number - 1),
                    "ppt_slide_number": slide_number, "ppt_slide_title": title,
                    "ppt_detected_title": detected_title, "ppt_title_number": hint["number"],
                    "ppt_title_level_hint": int(hint["level"]),
                    "ppt_title_number_ambiguous": bool(hint["ambiguous"]),
                    "ppt_title_inherited": inherited, "ppt_part_index": part_index,
                    "ppt_layout_kind": str(slide.get("layout_kind") or "single_column"),
                    "ppt_slide_type": slide_type,
                })
        return chunks

    @staticmethod
    def _ppt_page_rows(slides: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for slide in slides:
            slide_type = str(slide.get("slide_type") or "SIMPLE_CONTENT")
            page_type = slide_type if slide_type in {"COVER", "ENDING"} else "CONTENT"
            include = page_type not in {"COVER", "ENDING"}
            text_chars = sum(len(str(shape.get("text") or "")) for shape in slide.get("shapes") or [])
            rows.append({
                "page_index": int(slide.get("slide_index") or 0),
                "page_number": int(slide.get("slide_number") or int(slide.get("slide_index") or 0) + 1),
                "status": "PARSED_OK", "parse_method": "native", "page_type": page_type,
                "parse_level": str(slide.get("parse_level") or "FAST"),
                "native_text_chars": text_chars, "text_chars": text_chars, "parsed_text_chars": text_chars,
                "block_count": 0, "equation_count": 0, "table_count": 0,
                "image_count": int(slide.get("picture_count") or 0), "image_area_ratio": 0,
                "include_as_navigation": not include, "include_as_knowledge": include,
                "validation_issues": [], "error_message": "",
            })
        return rows

    @staticmethod
    def _suggest_material_type(name: str, markdown: str) -> tuple[str, str]:
        """Content-first, course-agnostic routing suggestion for teacher confirmation."""
        sample = f"{name}\n{markdown[:12000]}".lower()
        signals = {
            "syllabus": ("教学大纲", "课程大纲", "课程目标", "考核方式", "学时分配", "syllabus"),
            "lesson_plan": ("教案", "教学设计", "教学过程", "教学重点", "教学难点", "lesson plan"),
            "slides": ("幻灯片", "课件", "ppt", "powerpoint", "slide"),
            "textbook": ("教材", "本章小结", "课后思考", "参考文献", "textbook"),
            "experiment": ("实验目的", "实验步骤", "实验原理", "实验报告", "experiment"),
            "question_bank": ("题库", "选择题", "填空题", "参考答案", "答案解析", "question bank"),
            "knowledge_graph": ("知识图谱", "知识节点", "前置关系", "knowledge graph"),
            "teaching_schedule": ("教学进度", "授课进度", "周次", "教学日历", "teaching schedule"),
        }
        scores = {kind: sum(sample.count(word) for word in words) for kind, words in signals.items()}
        suffix = Path(name).suffix.lower()
        if suffix == ".pptx":
            scores["slides"] += 2
        best = max(scores, key=scores.get)
        if scores[best] <= 0:
            return "other", "未发现足够明确的资料用途信号，请教师确认"
        matched = [word for word in signals[best] if word in sample][:4]
        return best, f"根据正文结构与关键词建议：{', '.join(matched)}"

    def _ensure_material_metadata(self, document_id: str, name: str, markdown: str) -> None:
        suggested, reason = self._suggest_material_type(name, markdown)
        self.db.execute(
            """INSERT INTO document_material_metadata(
                   document_id,material_type,suggested_material_type,classification_status,
                   tags_json,classification_reason
               ) VALUES(?,?,?,'suggested','[]',?)
               ON CONFLICT(document_id) DO UPDATE SET
                   suggested_material_type=excluded.suggested_material_type,
                   material_type=CASE
                       WHEN document_material_metadata.classification_status='confirmed'
                       THEN document_material_metadata.material_type
                       ELSE excluded.material_type
                   END,
                   classification_reason=excluded.classification_reason,
                   updated_at=CURRENT_TIMESTAMP""",
            (document_id, suggested, suggested, reason),
        )

    def _upsert_artifact(self, document_id: str, artifact_type: str, *, path: Path | None = None,
                         status: str, generator_name: str, generator_version: str = "1",
                         error_message: str = "") -> dict[str, Any]:
        digest = ""
        stored_path = ""
        if path is not None and path.is_file():
            stored_path = str(path.resolve())
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        artifact_id = f"artifact_{uuid.uuid4().hex}"
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO document_artifacts(artifact_id,document_id,artifact_type,stored_path,
                   content_sha256,status,generator_name,generator_version,error_message)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(document_id,artifact_type) DO UPDATE SET
                   stored_path=excluded.stored_path,content_sha256=excluded.content_sha256,
                   status=excluded.status,generator_name=excluded.generator_name,
                   generator_version=excluded.generator_version,error_message=excluded.error_message,
                   updated_at=CURRENT_TIMESTAMP""",
                (artifact_id, document_id, artifact_type, stored_path, digest, status,
                 generator_name, generator_version, error_message[:1000]),
            )
        return self.db.fetch_one(
            "SELECT * FROM document_artifacts WHERE document_id=? AND artifact_type=?",
            (document_id, artifact_type),
        ) or {}

    def _write_canonical_markdown(self, job: dict[str, Any], markdown: str,
                                  generator_name: str, generator_version: str) -> Path:
        path = Path(job["stored_path"])
        destination = path.parent / f"{job['document_id']}_canonical.md"
        destination.write_text(markdown.strip() + "\n", encoding="utf-8")
        self._upsert_artifact(
            job["document_id"], "canonical_markdown", path=destination, status="ready",
            generator_name=generator_name, generator_version=generator_version,
        )
        return destination

    def _ensure_canonical_artifact(self, document_id: str, blocks: list[dict[str, Any]]) -> None:
        existing = self.db.fetch_one(
            """SELECT 1 ok FROM document_artifacts WHERE document_id=?
               AND artifact_type='canonical_markdown' AND status='ready'""", (document_id,),
        )
        if existing:
            return
        document = self.db.fetch_one(
            "SELECT document_id,stored_path FROM course_documents WHERE document_id=?", (document_id,),
        )
        if not document:
            return
        markdown = "\n\n".join(
            str(row.get("markdown") or row.get("latex") or row.get("plain_text") or "").strip()
            for row in blocks
            if str(row.get("markdown") or row.get("latex") or row.get("plain_text") or "").strip()
        )
        if markdown:
            parser = next((str(row.get("parser_name") or "") for row in blocks if row.get("parser_name")), "DocumentIR")
            version = next((str(row.get("parser_version") or "") for row in blocks if row.get("parser_version")), "1")
            self._write_canonical_markdown(document, markdown, parser, version)

    def _create_office_preview(self, job: dict[str, Any]) -> None:
        source = Path(job["stored_path"])
        # DOCX has a safe native HTML renderer below and must not depend on a
        # server-side office installation. PPTX is rendered in the browser.
        if source.suffix.lower() != ".pptx":
            return
        converter = shutil.which("soffice") or shutil.which("libreoffice")
        if not converter:
            self._upsert_artifact(
                job["document_id"], "preview_pdf", status="unavailable",
                generator_name="libreoffice", error_message="服务器未安装 LibreOffice，无法生成 Office 预览",
            )
            return
        preview_dir = source.parent / f"{job['document_id']}_preview"
        preview_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                [converter, "--headless", "--convert-to", "pdf", "--outdir", str(preview_dir), str(source)],
                capture_output=True, text=True, timeout=300, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            generated = preview_dir / f"{source.stem}.pdf"
            if result.returncode != 0 or not generated.is_file():
                detail = (result.stderr or result.stdout or "LibreOffice 未生成 PDF").strip()
                raise RuntimeError(detail)
            destination = preview_dir / "preview.pdf"
            if generated != destination:
                generated.replace(destination)
            self._upsert_artifact(
                job["document_id"], "preview_pdf", path=destination, status="ready",
                generator_name="libreoffice",
            )
        except Exception as exc:
            self._upsert_artifact(
                job["document_id"], "preview_pdf", status="failed", generator_name="libreoffice",
                error_message=str(exc),
            )

    @staticmethod
    def _native_markdown(path: Path, chunks: list[dict[str, Any]]) -> str:
        if path.suffix.lower() in {".md", ".txt"}:
            return path.read_text(encoding="utf-8-sig")
        lines: list[str] = []
        previous_section = ""
        for chunk in chunks:
            section = str(chunk.get("section") or "").strip()
            if section and section != previous_section:
                level = max(1, min(3, int(chunk.get("heading_level") or 2)))
                lines.extend([f"{'#' * level} {section}", ""])
                previous_section = section
            content = str(chunk.get("content") or "").strip()
            if content:
                lines.extend([content, ""])
        return "\n".join(lines).strip()

    def queue_document(self, actor: dict[str, Any], course_id: str, name: str,
                       mime_type: str, data: bytes, *, analysis_mode: str = "api",
                       ai_settings: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.queue_document_stream(
            actor, course_id, name, mime_type, io.BytesIO(data),
            analysis_mode=analysis_mode, ai_settings=ai_settings,
        )

    def queue_teaching_archive_document_stream(
        self, actor: dict[str, Any], course_id: str, name: str, mime_type: str,
        stream: BinaryIO, class_ids: list[str], *, analysis_mode: str = "api",
    ) -> dict[str, Any]:
        unique_class_ids = list(dict.fromkeys(str(value) for value in class_ids if str(value)))
        if not unique_class_ids:
            raise ValidationError("请至少选择一个适用教学班")
        teacher_id = str(actor.get("user_id") or "")
        placeholders = ",".join("?" for _ in unique_class_ids)
        matched = self.db.fetch_all(
            f"""SELECT class_id FROM classes WHERE class_id IN ({placeholders})
               AND course_id=? AND teacher_id=?""",
            (*unique_class_ids, course_id, teacher_id),
        )
        if {str(row["class_id"]) for row in matched} != set(unique_class_ids):
            raise PermissionDenied("只能把大纲分配给当前课程中自己管理的教学班")
        job = self.queue_document_stream(
            actor, course_id, name, mime_type, stream, analysis_mode=analysis_mode,
        )
        document_id = str(job["document_id"])
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO document_material_metadata(
                       document_id,material_type,suggested_material_type,classification_status,
                       tags_json,classification_reason,classified_by,classified_at
                   ) VALUES(?,'syllabus','syllabus','confirmed','[]',
                            '从教学档案上传，明确归类为班级教学大纲',?,CURRENT_TIMESTAMP)
                   ON CONFLICT(document_id) DO UPDATE SET
                       material_type='syllabus',suggested_material_type='syllabus',
                       classification_status='confirmed',
                       classification_reason='从教学档案上传，明确归类为班级教学大纲',
                       classified_by=excluded.classified_by,
                       classified_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP""",
                (document_id, teacher_id),
            )
            conn.executemany(
                """INSERT INTO teaching_archive_document_assignments(
                       assignment_id,document_id,class_id,assigned_by
                   ) VALUES(?,?,?,?)""",
                [(f"tada_{uuid.uuid4().hex}", document_id, class_id, teacher_id)
                 for class_id in unique_class_ids],
            )
        return {**job, "class_ids": unique_class_ids, "material_type": "syllabus"}

    def queue_document_stream(self, actor: dict[str, Any], course_id: str, name: str,
                              mime_type: str, stream: BinaryIO, *,
                              relative_path: str = "",
                              analysis_mode: str = "api",
                              ai_settings: dict[str, Any] | None = None) -> dict[str, Any]:
        if actor.get("role") != "teacher":
            raise PermissionDenied("仅教师可以建设共享知识库")
        teacher_id = str(actor["user_id"])
        course = self.campus.require_access(course_id, teacher_id, "teacher")
        if course["course_type"] != "shared_course" or course["owner_id"] != teacher_id:
            raise PermissionDenied("只能向自己的共享课程上传资料")
        if analysis_mode not in {"api", "local"}:
            raise ValidationError("资料分析方式必须是 api 或 local")
        settings = self._resolve_ai_settings(actor, ai_settings)
        custom_key = settings["api_key"]
        custom_base = settings["base_url"]
        custom_model = settings["model"]
        custom_provider = settings["provider"]
        custom_enabled = bool(
            custom_base and custom_model and (custom_key or custom_provider == "ollama")
        )
        safe_name = _safe_name(name)
        clean_relative_path = str(relative_path or "").replace("\\", "/").strip("/")
        if clean_relative_path:
            relative_parts = [part for part in clean_relative_path.split("/") if part]
            if any(part in {".", ".."} for part in relative_parts) or len(relative_parts) > 12:
                raise ValidationError("来源目录路径不安全")
            clean_relative_path = "/".join(_safe_name(part) for part in relative_parts)
        suffix = Path(safe_name).suffix.lower()
        if suffix not in ALLOWED_FILES or mime_type not in ALLOWED_FILES[suffix]:
            raise ValidationError("扩展名与 MIME 类型不匹配或不受支持")
        document_id = f"doc_{uuid.uuid4().hex}"
        job_id = f"job_{uuid.uuid4().hex}"
        batch_size = int(get_document_ingestion_settings().get("batch_size") or DEFAULT_BATCH_SIZE)
        root = (self.campus.storage_dir / course_id).resolve()
        destination = (root / f"{document_id}_{safe_name}").resolve()
        if root not in destination.parents:
            raise ValidationError("文件路径不安全")
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = destination.parent / f".{document_id}.uploading"
        digest_builder = hashlib.sha256()
        size_bytes = 0
        try:
            with staging.open("wb") as output:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    size_bytes += len(chunk)
                    if size_bytes > MAX_UPLOAD_BYTES:
                        raise ValidationError(f"文件不能超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB")
                    digest_builder.update(chunk)
                    output.write(chunk)
            if not size_bytes:
                raise ValidationError("不能上传空文件")
            try:
                validate_document_path(safe_name, staging, max_bytes=MAX_UPLOAD_BYTES)
            except UnsafeUpload as exc:
                raise ValidationError(str(exc)) from exc
            digest = digest_builder.hexdigest()
            if self.db.fetch_one(
                "SELECT 1 ok FROM course_documents WHERE course_id=? AND sha256=?", (course_id, digest)
            ):
                raise ValidationError("该课程中已存在内容相同的文件")
            staging.replace(destination)
            with self.db.connect() as conn:
                conn.execute(
                    """INSERT INTO course_documents(document_id,course_id,uploader_id,original_name,stored_path,mime_type,size_bytes,sha256,status,source_relative_path)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (document_id, course_id, teacher_id, safe_name, str(destination), mime_type, size_bytes, digest, "queued", clean_relative_path),
                )
                conn.execute(
                    """INSERT INTO ingestion_jobs(job_id,document_id,course_id,requested_by,status,
                           parser_config_hash,analysis_mode,ai_provider,ai_base_url,ai_model,ai_key_encrypted,batch_size)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (job_id, document_id, course_id, teacher_id, "queued",
                     hashlib.sha256(
                         f"teacher-adaptive-pdf-v1-batch-{batch_size}".encode("utf-8")
                     ).hexdigest(), analysis_mode, custom_provider if custom_enabled else "",
                     custom_base if custom_enabled else "", custom_model if custom_enabled else "",
                     encrypt_job_secret(custom_key), batch_size),
                )
        except Exception:
            staging.unlink(missing_ok=True)
            if not self.db.fetch_one("SELECT 1 ok FROM course_documents WHERE document_id=?", (document_id,)):
                destination.unlink(missing_ok=True)
            raise
        return self.get_job(actor, job_id)

    def process_job(self, job_id: str) -> None:
        job = self.db.fetch_one(
            "SELECT j.*,d.stored_path,d.original_name FROM ingestion_jobs j JOIN course_documents d USING(document_id) WHERE job_id=?",
            (job_id,),
        )
        if not job or job["status"] not in {"queued", "running"}:
            return
        self.db.execute("UPDATE ingestion_jobs SET status='running',updated_at=CURRENT_TIMESTAMP WHERE job_id=?", (job_id,))
        self.db.execute("UPDATE course_documents SET status='processing' WHERE document_id=?", (job["document_id"],))
        try:
            path = Path(job["stored_path"])
            suffix = path.suffix.lower()
            title_rebuild_only = str(job.get("pipeline_stage") or "") == "PPT_TITLE_REBUILD"
            chunks: list[dict[str, Any]] = []
            blocks: list[dict[str, Any]] = []
            parser_name = "zhijiao-native"
            parser_version = "1"
            canonical_markdown = ""
            adaptive_result = None
            structure: dict[str, Any] = {"status": "ok", "outline": [], "toc_entries": [], "warnings": []}
            ppt_slides: list[dict[str, Any]] = []
            native_page_rows: list[dict[str, Any]] = []
            if suffix == ".pdf":
                if not self.mineru.enabled:
                    raise ValidationError(
                        "PDF 必须通过 MinerU 解析；请配置远程 ZHIJIAO_MINERU_URL 后重试"
                    )
                adaptive_result = self._process_pdf_adaptive(job, path)
                blocks = adaptive_result.blocks
                parser_name = "MinerU"
                parser_version = adaptive_result.parser_version
                canonical_markdown = adaptive_result.canonical_markdown.strip()
            else:
                if suffix == ".pptx":
                    ppt_slides = self._inspect_ppt_fast(job, path)
                    chunks = self._ppt_chunks_from_slides(ppt_slides)
                    native_page_rows = self._ppt_page_rows(ppt_slides)
                else:
                    chunks = parse_document(path.read_bytes(), suffix)
            if chunks:
                previous_heading = ""
                for chunk in chunks:
                    content = chunk["content"]
                    heading = str(chunk.get("section") or "").strip()
                    heading_level = int(chunk.get("heading_level") or 0)
                    if heading_level and heading and heading != previous_heading:
                        title_raw = dict(chunk)
                        title_raw["is_slide_title_block"] = suffix == ".pptx"
                        blocks.append({
                            "block_type": "title", "markdown": f"{'#' * min(heading_level, 3)} {heading}",
                            "plain_text": heading, "latex": "",
                            "page_number": int(chunk.get("page_number") or 1), "bbox": [],
                            "confidence": None, "source_method": "native", "verification_status": "auto_verified",
                            "search_aliases": search_aliases(heading), "source_image_path": "", "raw": title_raw,
                        })
                        previous_heading = heading
                    if str(content).strip():
                        content_raw = dict(chunk)
                        content_raw["is_slide_title_block"] = False
                        blocks.append({
                            "block_type": "paragraph", "markdown": content, "plain_text": content,
                            "latex": "", "page_number": int(chunk.get("page_number") or 1), "bbox": [],
                            "confidence": None, "source_method": "native", "verification_status": "review_required",
                            "search_aliases": search_aliases(content), "source_image_path": "", "raw": content_raw,
                        })
                canonical_markdown = self._native_markdown(path, chunks)
            elif suffix != ".pdf" and self.mineru.enabled:
                middle = self.mineru.parse(
                    path, method="ocr", asset_dir=path.parent / f"{job['document_id']}_assets"
                )
                blocks = mineru_to_blocks(middle)
                parser_name = "MinerU"
                parser_version = str(middle.get("_version_name") or "unknown")
                canonical_markdown = str(middle.get("_markdown") or "").strip()
                if self.formula.enabled:
                    for block in blocks:
                        source_image = block.get("source_image_path")
                        if block["block_type"] != "formula" or not source_image:
                            continue
                        secondary = self.formula.recognize(Path(source_image))
                        secondary_latex = str(secondary.get("latex") or "")
                        consistent = normalize_latex(block["latex"]) == normalize_latex(secondary_latex)
                        block["raw"]["formula_secondary_latex"] = secondary_latex
                        block["raw"]["formula_consistent"] = consistent
                        block["raw"]["formula_secondary_engine"] = secondary.get("engine", "pix2text")
                        if consistent and not formula_anomalies(block["latex"]):
                            block["verification_status"] = "auto_verified"
            if not blocks:
                hint = "；请配置远程 MinerU 后重试" if not self.mineru.enabled else ""
                raise ValidationError(
                    f"文件未生成 DocumentIR 块，解析任务未进入 AI 分析{hint}"
                )
            if not canonical_markdown:
                canonical_markdown = "\n\n".join(
                    str(block.get("markdown") or block.get("latex") or block.get("plain_text") or "").strip()
                    for block in blocks
                    if str(block.get("markdown") or block.get("latex") or block.get("plain_text") or "").strip()
                )
            blocks, structure = self._annotate_source_blocks(
                blocks, adaptive_result.pages if adaptive_result is not None else native_page_rows,
                job["document_id"],
            )
            if adaptive_result is not None:
                self._write_normalized_structure(adaptive_result.output_root, blocks, structure)
            self._write_canonical_markdown(job, canonical_markdown, parser_name, parser_version)
            self._ensure_material_metadata(
                job["document_id"], job["original_name"], canonical_markdown
            )
            self._create_office_preview(job)
            by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for block in blocks:
                block.setdefault("block_id", f"block_{uuid.uuid4().hex}")
                page_number = int(block["page_number"])
                by_page[page_number].append(block)
            with self.db.connect() as conn:
                conn.execute("DELETE FROM document_chunks WHERE document_id=?", (job["document_id"],))
                conn.execute("DELETE FROM document_pages WHERE document_id=?", (job["document_id"],))
                conn.execute("DELETE FROM document_blocks WHERE document_id=?", (job["document_id"],))
                conn.executemany(
                    "INSERT INTO document_chunks(document_id,course_id,section,page_number,content) VALUES(?,?,?,?,?)",
                    [(job["document_id"], job["course_id"], x["block_type"], x["page_number"], x["plain_text"])
                     for x in blocks if x["plain_text"]],
                )
                order = 0
                pending_count = 0
                if adaptive_result is not None:
                    page_rows = adaptive_result.pages
                elif native_page_rows:
                    page_rows = native_page_rows
                else:
                    page_rows = [
                        {
                            "page_index": page_number - 1, "page_number": page_number,
                            "status": "PARSED_OK", "parse_method": page_blocks[0]["source_method"],
                            "page_type": "CONTENT", "parse_level": "NORMAL",
                            "native_text_chars": sum(len(x.get("plain_text", "")) for x in page_blocks),
                            "text_chars": sum(len(x.get("plain_text", "")) for x in page_blocks),
                            "parsed_text_chars": sum(len(x.get("plain_text", "")) for x in page_blocks),
                            "block_count": len(page_blocks), "equation_count": sum(x["block_type"] == "formula" for x in page_blocks),
                            "table_count": sum(x["block_type"] == "table" for x in page_blocks), "image_count": sum(x["block_type"] == "image" for x in page_blocks),
                            "image_area_ratio": 0, "include_as_navigation": False, "include_as_knowledge": True,
                            "validation_issues": [], "error_message": "",
                        }
                        for page_number, page_blocks in sorted(by_page.items())
                    ]
                page_ids: dict[int, str] = {}
                for page_row in sorted(page_rows, key=lambda row: int(row["page_index"])):
                    page_index = int(page_row["page_index"])
                    page_number = int(page_row["page_number"])
                    page_blocks = by_page.get(page_number, [])
                    page_id = f"page_{uuid.uuid4().hex}"
                    page_ids[page_index] = page_id
                    page_pending = any(
                        x["verification_status"] == "review_required"
                        and page_row.get("include_as_knowledge", True)
                        for x in page_blocks
                    )
                    adaptive_status = str(page_row.get("status") or "PARSED_OK")
                    page_status = {
                        "PARSED_OK": "ready", "PARSED_PARTIAL": "review_required",
                        "TEXT_ONLY": "review_required", "SUSPECT": "review_required",
                        "FAILED": "failed", "PENDING": "queued", "PROCESSING": "queued",
                    }.get(adaptive_status, "review_required")
                    if page_pending and page_status == "ready":
                        page_status = "review_required"
                    conn.execute(
                        """INSERT INTO document_pages(
                               page_id,document_id,page_number,page_index,batch_number,status,parse_method,
                               page_type,parse_level,native_text_chars,text_chars,parsed_text_chars,block_count,
                               equation_count,table_count,image_count,image_area_ratio,include_as_navigation,
                               include_as_knowledge,validation_issues_json,error_message)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (page_id, job["document_id"], page_number, page_index,
                         (page_index // int(job.get("batch_size") or 40)) + 1, page_status,
                         page_row.get("parse_method") or (page_blocks[0]["source_method"] if page_blocks else "skip"),
                         page_row.get("page_type", "CONTENT"), page_row.get("parse_level", "NORMAL"),
                         int(page_row.get("native_text_chars") or 0), int(page_row.get("text_chars") or 0),
                         int(page_row.get("parsed_text_chars") or 0), int(page_row.get("block_count") or len(page_blocks)),
                         int(page_row.get("equation_count") or 0), int(page_row.get("table_count") or 0),
                         int(page_row.get("image_count") or 0), float(page_row.get("image_area_ratio") or 0),
                         int(bool(page_row.get("include_as_navigation"))), int(bool(page_row.get("include_as_knowledge", True))),
                         json.dumps(page_row.get("validation_issues") or [], ensure_ascii=False),
                         str(page_row.get("error_message") or "")[:1000]),
                    )
                    for block in page_blocks:
                        order += 1
                        if block["verification_status"] == "review_required":
                            pending_count += 1
                        block_page_index = int(block.get("page_index", page_number - 1))
                        include_as_knowledge = bool(block.get("include_as_knowledge", page_row.get("include_as_knowledge", True)))
                        conn.execute(
                            """INSERT INTO document_blocks(block_id,document_id,page_id,block_order,block_type,markdown,plain_text,
                               latex,source_image_path,page_number,bbox_json,confidence,source_method,verification_status,parser_name,parser_version,
                               search_aliases_json,raw_payload_json,page_index,page_type,parse_level,chapter_path_json,
                               include_as_navigation,include_as_knowledge,content_destination,semantic_role,
                               region_type,region_confidence,region_reason,parent_region_block_id,knowledge_candidate)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (block["block_id"], job["document_id"], page_id, order, block["block_type"],
                             block["markdown"], block["plain_text"], block["latex"], block.get("source_image_path", ""), page_number,
                             json.dumps(block["bbox"], ensure_ascii=False), block["confidence"], block["source_method"],
                             block["verification_status"], parser_name, parser_version,
                             json.dumps(block["search_aliases"], ensure_ascii=False),
                             json.dumps(block["raw"], ensure_ascii=False), block_page_index,
                             block.get("page_type", page_row.get("page_type", "CONTENT")),
                             block.get("parse_level", page_row.get("parse_level", "NORMAL")),
                             json.dumps(block.get("chapter_path") or [], ensure_ascii=False),
                             int(bool(block.get("include_as_navigation", page_row.get("include_as_navigation", False)))),
                             int(include_as_knowledge),
                             str(block.get("content_destination") or ("unclassified" if include_as_knowledge else "excluded")),
                             str(block.get("semantic_role") or ("" if include_as_knowledge else "navigation")),
                             str(block.get("region_type") or "knowledge"), float(block.get("region_confidence") or 0.45),
                             str(block.get("region_reason") or ""), str(block.get("parent_region_block_id") or ""),
                             int(bool(block.get("knowledge_candidate", False)))),
                        )
                final_status = (
                    adaptive_result.job_status if adaptive_result is not None
                    else ("review_required" if pending_count else "ready")
                )
                conn.execute(
                    """UPDATE ingestion_jobs SET status=?,progress=100,total_pages=?,completed_pages=?,
                       failed_pages=?,pipeline_stage=?,manifest_path=?,batch_size=?,updated_at=CURRENT_TIMESTAMP WHERE job_id=?""",
                    (final_status,
                     len(page_rows),
                     sum(str(row.get("status")) != "FAILED" for row in page_rows),
                     sum(str(row.get("status")) == "FAILED" for row in page_rows),
                     "WAITING_REVIEW" if final_status == "review_required" else ("FAILED" if final_status == "failed" else "COMPLETED"),
                     str(adaptive_result.manifest_path) if adaptive_result is not None else "",
                     int(job.get("batch_size") or DEFAULT_BATCH_SIZE), job_id),
                )
                conn.execute("UPDATE course_documents SET status=?,error_message='' WHERE document_id=?",
                             (final_status, job["document_id"]))
                if adaptive_result is not None:
                    for key, batch in adaptive_result.manifest.payload.get("batches", {}).items():
                        conn.execute(
                            """INSERT INTO document_batches(
                                   batch_id,document_id,batch_number,original_page_start,original_page_end,status,
                                   retry_count,completed_pages,error_message,artifact_path)
                               VALUES(?,?,?,?,?,?,?,?,?,?)
                               ON CONFLICT(document_id,batch_number) DO UPDATE SET
                                   status=excluded.status,retry_count=excluded.retry_count,
                                   completed_pages=excluded.completed_pages,error_message=excluded.error_message,
                                   artifact_path=excluded.artifact_path,updated_at=CURRENT_TIMESTAMP""",
                            (f"{job['document_id']}_batch_{int(key):03d}", job["document_id"], int(key),
                             int(batch["original_page_start"]), int(batch["original_page_end"]), batch["status"],
                             int(batch.get("retry_count") or 0), int(batch.get("completed_pages") or 0),
                             str(batch.get("error_message") or "")[:1000],
                             str(adaptive_result.output_root / "normalized" / f"batch_{int(key):03d}.jsonl")),
                        )
            self._persist_document_structure(job["document_id"], structure)
            self._rebuild_knowledge_candidates(job["document_id"], job["course_id"])
            if title_rebuild_only:
                latest_analysis = self.db.fetch_one(
                    """SELECT analysis_job_id FROM semantic_analysis_jobs
                       WHERE document_id=? AND status IN ('review_required','completed')
                       ORDER BY created_at DESC LIMIT 1""",
                    (job["document_id"],),
                ) or {}
                analysis_job_id = latest_analysis.get("analysis_job_id")
                created = self._rebuild_document_outline_from_candidates(
                    job, analysis_job_id, replace_existing=True
                )
                if created:
                    self._rebuild_course_outline(
                        job["course_id"], analysis_job_id, use_api=False,
                        material_type=self._document_material_type(job["document_id"]),
                    )
            self.db.execute(
                """INSERT INTO semantic_analysis_jobs(
                       analysis_job_id,document_id,course_id,requested_by,status,current_stage,analysis_mode,
                       ai_provider,ai_base_url,ai_model,ai_key_encrypted
                   ) VALUES(?,?,?,?, 'queued','queued',?,?,?,?,?)""",
                (f"saj_{uuid.uuid4().hex}", job["document_id"], job["course_id"],
                 job["requested_by"], job.get("analysis_mode") or "api",
                 job.get("ai_provider") or "", job.get("ai_base_url") or "",
                 job.get("ai_model") or "", job.get("ai_key_encrypted") or ""),
            ) if (
                blocks and not title_rebuild_only
                and (adaptive_result is None or adaptive_result.job_status != "failed")
            ) else None
        except Exception as exc:
            message = str(exc)[:1000]
            self.db.execute(
                "UPDATE ingestion_jobs SET status='failed',error_message=?,updated_at=CURRENT_TIMESTAMP WHERE job_id=?",
                (message, job_id),
            )
            self.db.execute("UPDATE course_documents SET status='failed',error_message=? WHERE document_id=?", (message, job["document_id"]))

    @staticmethod
    def _json_response(raw: str) -> dict[str, Any]:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I | re.S)
        value = json.loads(cleaned)
        if isinstance(value, list):
            return {"blocks": value}
        if not isinstance(value, dict):
            raise ValueError("AI 分类结果必须是 JSON 对象")
        return value

    def _classify_document(self, document_id: str, course_id: str) -> None:
        """Classify immutable parser blocks; failures deliberately remain reviewable."""
        blocks = self.db.fetch_all(
            """SELECT block_id,block_type,page_number,block_order,markdown,plain_text,latex,
                      source_image_path,bbox_json FROM document_blocks
               WHERE document_id=? ORDER BY page_number,block_order""", (document_id,),
        )
        if not blocks:
            return
        classifications: list[dict[str, Any]] = []
        try:
            provider = self.campus.provider_factory()
            if provider is None:
                raise RuntimeError("AI Provider 不可用")
            for start in range(0, len(blocks), 40):
                batch = blocks[start:start + 40]
                compact = [{
                    "block_id": row["block_id"], "type": row["block_type"],
                    "page": row["page_number"], "order": row["block_order"],
                    "content": (row["markdown"] or row["latex"] or row["plain_text"])[:3000],
                } for row in batch]
                prompt = (
                    "请按文档顺序分析以下 DocumentIR 块。输出严格 JSON：{\"blocks\":[{\"block_id\":...,"
                    "\"content_destination\":\"knowledge|question_bank|excluded\",\"semantic_role\":"
                    "\"definition|principle|explanation|code|formula|question|example|answer|solution|question_attachment|decoration\","
                    "\"confidence\":0-1,\"reason\":...,\"question_group_key\":...}]}。"
                    "定义、原理、说明、代码知识和知识公式进 knowledge；例题、习题、答案、解析及题目依赖图表进 question_bank，"
                    "同一题使用相同 question_group_key；页眉页脚、装饰图和无关图表进 excluded。\n"
                    + json.dumps(compact, ensure_ascii=False)
                )
                response = provider.generate(
                    "你是教材结构分析器，只分类已有内容，不补写题目或答案。必须返回可解析 JSON。", prompt
                )
                parsed = self._json_response(response)
                classifications.extend(parsed.get("blocks") or parsed.get("classifications") or [])
        except Exception as exc:
            self.db.execute(
                """UPDATE document_blocks SET content_destination='unclassified',semantic_role='',
                   analysis_confidence=NULL,analysis_reason=? WHERE document_id=?""",
                (f"AI 分类不可用：{str(exc)[:300]}", document_id),
            )
            return
        known = {row["block_id"]: row for row in blocks}
        seen: set[str] = set()
        with self.db.connect() as conn:
            for item in classifications:
                block_id = str(item.get("block_id") or "")
                if block_id not in known:
                    continue
                destination = str(item.get("content_destination") or item.get("destination") or "unclassified")
                if destination not in {"knowledge", "question_bank", "excluded"}:
                    destination = "unclassified"
                block_type = known[block_id]["block_type"]
                if destination == "knowledge" and block_type in {"image", "table"}:
                    destination = "excluded"
                role = str(item.get("semantic_role") or "")[:64]
                group = str(item.get("question_group_key") or "")[:100]
                if destination == "question_bank" and not group:
                    group = f"page-{known[block_id]['page_number']}-item-{known[block_id]['block_order']}"
                try:
                    confidence = max(0.0, min(1.0, float(item.get("confidence", 0))))
                except (TypeError, ValueError):
                    confidence = None
                verification = "rejected" if destination == "excluded" else "review_required"
                conn.execute(
                    """UPDATE document_blocks SET content_destination=?,semantic_role=?,analysis_confidence=?,
                       analysis_reason=?,question_group_key=?,verification_status=?,
                       updated_at=CURRENT_TIMESTAMP WHERE block_id=?""",
                    (destination, role, confidence, str(item.get("reason") or "")[:500], group,
                     verification, block_id),
                )
                seen.add(block_id)
            for block_id in known.keys() - seen:
                conn.execute(
                    "UPDATE document_blocks SET content_destination='unclassified',analysis_reason='AI 未返回该块分类' WHERE block_id=?",
                    (block_id,),
                )
        self._rebuild_question_drafts(document_id, course_id)

    def _rebuild_question_drafts(self, document_id: str, course_id: str) -> None:
        # 教材/PPT 中识别出的例题只用于内容分流，不再自动进入正式题库。
        # 正式题库只能由教师通过题库模板导入、审核并发布。
        try:
            self.db.execute(
                """DELETE FROM question_bank_items
                   WHERE document_id=? AND status='draft'
                     AND source_kind='document_extracted'""",
                (document_id,),
            )
            return
        except Exception as exc:
            # 兼容尚未执行 009 迁移的数据库；迁移完成后会走上面的明确来源约束。
            if "source_kind" not in str(exc):
                raise
            self.db.execute(
                "DELETE FROM question_bank_items WHERE document_id=? AND status='draft'",
                (document_id,),
            )
            return
        rows = self.db.fetch_all(
            """SELECT * FROM document_blocks WHERE document_id=? AND content_destination='question_bank'
               ORDER BY page_number,block_order""", (document_id,),
        )
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[row["question_group_key"]].append(row)
        with self.db.connect() as conn:
            existing_drafts = conn.execute(
                "SELECT item_id,question_group_key FROM question_bank_items WHERE document_id=? AND status='draft'",
                (document_id,),
            ).fetchall()
            for draft in existing_drafts:
                if draft["question_group_key"] not in groups:
                    conn.execute("DELETE FROM question_bank_items WHERE item_id=?", (draft["item_id"],))
            for group, parts in groups.items():
                existing = conn.execute(
                    "SELECT item_id,status FROM question_bank_items WHERE document_id=? AND question_group_key=?",
                    (document_id, group),
                ).fetchone()
                item_id = existing["item_id"] if existing else f"qbi_{uuid.uuid4().hex}"
                def joined(*roles: str) -> str:
                    return "\n\n".join((row["markdown"] or row["latex"] or row["plain_text"]).strip()
                                        for row in parts if row["semantic_role"] in roles and
                                        (row["markdown"] or row["latex"] or row["plain_text"]).strip())
                stem = joined("question", "example", "stem")
                answer = joined("answer")
                explanation = joined("solution", "explanation")
                if not stem:
                    stem = joined("formula", "code") or next(
                        ((row["markdown"] or row["plain_text"]) for row in parts
                         if row["block_type"] not in {"image", "table"}), ""
                    )
                pages = sorted({int(row["page_number"] or 1) for row in parts})
                combined = " ".join(str(row["plain_text"] or row["markdown"]) for row in parts)
                question_type = "single_choice" if re.search(r"(?:^|\s)[A-D][.、]", combined) else (
                    "true_false" if "判断" in combined else "short_answer"
                )
                if existing:
                    if existing["status"] == "draft":
                        conn.execute(
                            """UPDATE question_bank_items SET question_type=?,stem_markdown=?,answer_markdown=?,explanation_markdown=?,
                               source_pages_json=?,updated_at=CURRENT_TIMESTAMP WHERE item_id=?""",
                            (question_type, stem, answer, explanation, json.dumps(pages), item_id),
                        )
                else:
                    conn.execute(
                        """INSERT INTO question_bank_items(item_id,course_id,document_id,question_group_key,
                           question_type,stem_markdown,answer_markdown,explanation_markdown,source_pages_json)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                        (item_id, course_id, document_id, group, question_type, stem, answer, explanation, json.dumps(pages)),
                    )
                for row in parts:
                    if row["block_type"] not in {"image", "table"}:
                        continue
                    conn.execute(
                        """INSERT OR IGNORE INTO question_bank_attachments(attachment_id,item_id,block_id,attachment_type,
                           source_image_path,page_number,bbox_json) VALUES(?,?,?,?,?,?,?)""",
                        (f"qba_{uuid.uuid4().hex}", item_id, row["block_id"], row["block_type"],
                         row["source_image_path"], row["page_number"], row["bbox_json"]),
                    )

    def queue_semantic_analysis(self, actor: dict[str, Any], document_id: str, *,
                                analysis_mode: str = "api",
                                ai_settings: dict[str, Any] | None = None) -> dict[str, Any]:
        document = self.require_document_access(actor, document_id)
        if actor.get("role") != "teacher":
            raise PermissionDenied("仅教师可以启动共享资料语义分析")
        if analysis_mode not in {"api", "local"}:
            raise ValidationError("资料分析方式必须是 api 或 local")
        settings = self._resolve_ai_settings(actor, ai_settings)
        custom_key = settings["api_key"]
        custom_base = settings["base_url"]
        custom_model = settings["model"]
        custom_provider = settings["provider"]
        custom_enabled = bool(
            custom_base and custom_model and (custom_key or custom_provider == "ollama")
        )
        active = self.db.fetch_one(
            """SELECT * FROM semantic_analysis_jobs WHERE document_id=?
               AND status IN ('queued','running','retry_wait') ORDER BY created_at DESC LIMIT 1""", (document_id,),
        )
        if active:
            active_uses_custom_api = bool(
                str(active.get("ai_provider") or "")
                and str(active.get("ai_base_url") or "")
                and str(active.get("ai_model") or "")
            )
            requested_uses_custom_api = custom_enabled
            same_configuration = (
                str(active.get("analysis_mode") or "api") == analysis_mode
                and active_uses_custom_api == requested_uses_custom_api
                and (
                    not requested_uses_custom_api
                    or (
                        str(active.get("ai_provider") or "") == custom_provider
                        and str(active.get("ai_base_url") or "") == custom_base
                        and str(active.get("ai_model") or "") == custom_model
                    )
                )
            )
            if same_configuration:
                return self.get_analysis_job(actor, active["analysis_job_id"])
            # Changing to local analysis or a custom provider is an explicit
            # replacement request. Never return an older retrying job whose
            # provider configuration differs from the teacher's selection.
            self.db.execute(
                """UPDATE semantic_analysis_jobs
                   SET status='cancelled',current_stage='cancelled',
                       next_retry_at=NULL,error_message=?,
                       updated_at=CURRENT_TIMESTAMP
                   WHERE analysis_job_id=?""",
                ("教师已使用新的分析方式重新发起任务", active["analysis_job_id"]),
            )
        if not self.db.fetch_one("SELECT 1 ok FROM document_blocks WHERE document_id=? LIMIT 1", (document_id,)):
            raise ValidationError("资料尚未完成 DocumentIR 解析")
        analysis_job_id = f"saj_{uuid.uuid4().hex}"
        self.db.execute(
            """INSERT INTO semantic_analysis_jobs(
                   analysis_job_id,document_id,course_id,requested_by,status,current_stage,analysis_mode,
                   ai_provider,ai_base_url,ai_model,ai_key_encrypted
               ) VALUES(?,?,?,?, 'queued','queued',?,?,?,?,?)""",
            (analysis_job_id, document_id, document["course_id"], actor["user_id"], analysis_mode,
             custom_provider if custom_enabled else "", custom_base if custom_enabled else "",
             custom_model if custom_enabled else "", encrypt_job_secret(custom_key)),
        )
        return self.get_analysis_job(actor, analysis_job_id)

    def _analysis_call(self, analysis_job_id: str) -> None:
        self.db.execute(
            """UPDATE semantic_analysis_jobs SET api_calls=api_calls+1,last_response_at=CURRENT_TIMESTAMP,
               updated_at=CURRENT_TIMESTAMP WHERE analysis_job_id=?""", (analysis_job_id,),
        )

    @staticmethod
    def _clean_title(value: Any, fallback: str) -> str:
        return str(value or fallback).strip()[:180] or fallback

    def _process_local_semantic_analysis(self, job: dict[str, Any],
                                         blocks: list[dict[str, Any]]) -> None:
        """Build a conservative evidence tree without calling any model provider."""
        analysis_job_id = str(job["analysis_job_id"])
        self.db.execute(
            """UPDATE semantic_analysis_jobs SET status='running',
               current_stage='local_content_filter',current_batch=0,total_batches=2,
               api_calls=0,error_message='',updated_at=CURRENT_TIMESTAMP
               WHERE analysis_job_id=?""",
            (analysis_job_id,),
        )
        question_pattern = re.compile(
            r"(?:^|\n)\s*(?:例题|习题|练习题|思考题|选择题|判断题|填空题|"
            r"参考答案|答案解析|问题\s*\d*|question\s*\d*)\s*[:：]?",
            re.I,
        )
        skipped = 0
        with self.db.connect() as conn:
            for block in blocks:
                text = str(
                    block.get("markdown") or block.get("latex") or block.get("plain_text") or ""
                ).strip()
                block_type = str(block.get("block_type") or "")
                if not text or block_type in {"image", "table"}:
                    destination, role, reason, verification = (
                        "excluded", "decoration", "本地模式跳过图片、表格或空白内容", "rejected"
                    )
                    skipped += 1
                elif question_pattern.search(text):
                    destination, role, reason, verification = (
                        "excluded", "question", "本地模式识别为例题或习题内容", "rejected"
                    )
                    skipped += 1
                else:
                    role = "formula" if block_type == "formula" else (
                        "code" if block_type == "code" else
                        "definition" if block_type == "title" else "explanation"
                    )
                    destination, reason, verification = (
                        "knowledge", "本地规则保留为待教师审核知识内容", "review_required"
                    )
                conn.execute(
                    """UPDATE document_blocks SET content_destination=?,semantic_role=?,
                       analysis_confidence=?,analysis_reason=?,verification_status=?,
                       updated_at=CURRENT_TIMESTAMP WHERE block_id=?""",
                    (destination, role, 1.0 if destination == "excluded" else 0.65,
                     reason, verification, block["block_id"]),
                )
            conn.execute(
                """DELETE FROM knowledge_nodes WHERE document_id=? AND node_scope='document'
                   AND NOT EXISTS (
                       SELECT 1 FROM knowledge_version_nodes vn
                       WHERE vn.node_id=knowledge_nodes.node_id
                   )""",
                (job["document_id"],),
            )
        self._build_faithful_document_outline(job, analysis_job_id)
        self.db.execute(
            """UPDATE semantic_analysis_jobs SET current_stage='local_course_outline',
               current_batch=1,updated_at=CURRENT_TIMESTAMP WHERE analysis_job_id=?""",
            (analysis_job_id,),
        )
        self._rebuild_course_outline(job["course_id"], analysis_job_id, use_api=False)
        result = {
            "schema_version": 4,
            "extractor": "local-evidence-outline",
            "skipped_blocks": skipped,
            "completed": True,
            "notice": "教师选择了仅本地分析，本任务未调用外部 AI API",
        }
        self.db.execute(
            """UPDATE semantic_analysis_jobs SET status='review_required',
               current_stage='teacher_review',current_batch=2,total_batches=2,
               api_calls=0,result_json=?,retry_count=0,next_retry_at=NULL,
               error_message='',updated_at=CURRENT_TIMESTAMP WHERE analysis_job_id=?""",
            (json.dumps(result, ensure_ascii=False), analysis_job_id),
        )

    def process_semantic_analysis(self, analysis_job_id: str) -> None:
        job = self.db.fetch_one("SELECT * FROM semantic_analysis_jobs WHERE analysis_job_id=?", (analysis_job_id,))
        if not job or job["status"] not in {"queued", "running", "retry_wait"}:
            return
        blocks = self.db.fetch_all(
            "SELECT * FROM document_blocks WHERE document_id=? ORDER BY page_number,block_order", (job["document_id"],),
        )
        if not blocks:
            self.db.execute(
                """UPDATE semantic_analysis_jobs SET status='failed',current_stage='failed',
                   error_message='资料没有 DocumentIR 块；请先完成文档解析后再分析',
                   updated_at=CURRENT_TIMESTAMP WHERE analysis_job_id=?""",
                (analysis_job_id,),
            )
            return
        self._enforce_navigation_exclusion(job["document_id"])
        self._ensure_canonical_artifact(job["document_id"], blocks)
        blocks, ppt_metadata_source = self._ensure_ppt_semantic_metadata(
            job["document_id"], blocks
        )
        if ppt_metadata_source:
            job["ppt_metadata_source"] = ppt_metadata_source
        blocks = [block for block in blocks if bool(block.get("include_as_knowledge", 1))]
        if not blocks:
            self.db.execute(
                """UPDATE semantic_analysis_jobs SET status='review_required',current_stage='teacher_review',
                   error_message='文档只有导航或非知识页，无可供知识分析的正文',updated_at=CURRENT_TIMESTAMP
                   WHERE analysis_job_id=?""",
                (analysis_job_id,),
            )
            return
        course = self.db.fetch_one(
            "SELECT course_type FROM courses WHERE course_id=?", (job["course_id"],)
        ) or {}
        if course.get("course_type") != "shared_course":
            self.db.execute(
                "UPDATE semantic_analysis_jobs SET status='failed',current_stage='failed',error_message=? WHERE analysis_job_id=?",
                ("教师语义分析只允许 shared_course", analysis_job_id),
            )
            return
        # Rebuild pending review boundaries with the current deterministic policy.
        # Approved/rejected candidates remain untouched by the rebuild.
        self._rebuild_knowledge_candidates(job["document_id"], job["course_id"])
        if str(job.get("analysis_mode") or "api") == "local":
            try:
                self._process_local_semantic_analysis(job, blocks)
            except Exception as exc:
                self.db.execute(
                    """UPDATE semantic_analysis_jobs SET status='failed',current_stage='failed',
                       error_message=?,updated_at=CURRENT_TIMESTAMP WHERE analysis_job_id=?""",
                    (str(exc)[:1200], analysis_job_id),
                )
            return
        # Shared-course knowledge trees always use the in-project evidence
        # pipeline. It consumes persisted Markdown and never imports Docling,
        # Torch, MinerU or an OCR runtime.
        default_semantic = self.semantic
        encrypted_key = str(job.get("ai_key_encrypted") or "")
        provider_name = str(job.get("ai_provider") or "").lower()
        base_url = str(job.get("ai_base_url") or "")
        model = str(job.get("ai_model") or "")
        if provider_name and base_url and model:
            api_key = decrypt_job_secret(encrypted_key)
            provider = self._custom_ai_provider(
                provider_name, api_key, base_url, model, timeout=115,
            )
            self.semantic = SemanticKnowledgeService(lambda: provider)
        try:
            self.semantic.preflight()
            self._process_evidence_tree_analysis(job, blocks)
        except Exception as exc:
            message = str(exc)[:1200]
            lowered = message.lower()
            transient = any(marker in lowered for marker in (
                "timeout", "timed out", "readtimeout", "connectionerror",
                "无法连接智能服务", "自动续跑", "qwen_connection_failed",
            ))
            retry_count = int(job.get("retry_count") or 0) + 1
            if transient and retry_count <= 6:
                delay_seconds = min(300, 15 * (2 ** (retry_count - 1)))
                self.db.execute(
                    """UPDATE semantic_analysis_jobs SET status='retry_wait',
                       current_stage='waiting_for_service',retry_count=?,
                       next_retry_at=datetime('now',?),error_message=?,
                       updated_at=CURRENT_TIMESTAMP WHERE analysis_job_id=?""",
                    (retry_count, f"+{delay_seconds} seconds",
                     f"智能服务暂时超时；已保存断点，将在 {delay_seconds} 秒后自动续跑"
                     f"（{retry_count}/6）", analysis_job_id),
                )
            else:
                self.db.execute(
                    """UPDATE semantic_analysis_jobs SET status='failed',current_stage='failed',
                       retry_count=?,next_retry_at=NULL,error_message=?,
                       updated_at=CURRENT_TIMESTAMP WHERE analysis_job_id=?""",
                    (retry_count, message, analysis_job_id),
                )
        finally:
            self.semantic = default_semantic
        return

        # Legacy implementations below are retained temporarily for database
        # compatibility tests, but are unreachable from production routing.
        if course.get("course_type") == "shared_course" and self.docling_graph.enabled:
            try:
                self._process_docling_graph_analysis(job, blocks)
            except Exception as exc:
                self.db.execute(
                    """UPDATE semantic_analysis_jobs SET status='failed',current_stage='failed',error_message=?,
                       updated_at=CURRENT_TIMESTAMP WHERE analysis_job_id=?""",
                    (str(exc)[:1200], analysis_job_id),
                )
            return
        # Every block is analyzed from the persisted canonical Markdown/DocumentIR.
        # This stage never reopens the source document and never invokes OCR.
        analysis_blocks = blocks
        has_explicit_outline = any(
            block.get("block_type") == "title" or self._heading_from_line(str(block.get("markdown") or ""))
            for block in blocks
        )
        batches: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_chars = 0
        for block in analysis_blocks:
            text = str(block.get("markdown") or block.get("latex") or block.get("plain_text") or "")[:2800]
            if current and (len(current) >= 16 or current_chars + len(text) > 22000):
                batches.append(current)
                current, current_chars = [], 0
            current.append(block)
            current_chars += len(text)
        if current:
            batches.append(current)
        total_batches = len(batches)
        previous_total = int(job.get("total_batches") or 0)
        resume_from = max(0, min(int(job.get("current_batch") or 0), total_batches))
        if previous_total and previous_total != total_batches:
            resume_from = 0
        self.db.execute(
            """UPDATE semantic_analysis_jobs SET status='running',current_stage='classification_and_chunking',
               total_batches=?,error_message='',updated_at=CURRENT_TIMESTAMP WHERE analysis_job_id=?""",
            (total_batches, analysis_job_id),
        )
        try:
            if resume_from == 0:
                with self.db.connect() as conn:
                    conn.execute(
                        """DELETE FROM knowledge_nodes WHERE analysis_job_id=?
                           AND NOT EXISTS (SELECT 1 FROM knowledge_version_nodes vn WHERE vn.node_id=knowledge_nodes.node_id)""",
                        (analysis_job_id,),
                    )
                    conn.execute(
                        """UPDATE document_blocks SET content_destination='knowledge',semantic_role='source_markdown',analysis_confidence=NULL,
                           analysis_reason='按原文标题与正文保留，待教师复核',question_group_key='',verification_status='review_required'
                           WHERE document_id=?""",
                        (job["document_id"],),
                    )
            existing_nodes = self.db.fetch_all(
                """SELECT n.*,p.title parent_title FROM knowledge_nodes n LEFT JOIN knowledge_nodes p ON p.node_id=n.parent_id
                   WHERE n.document_id=? AND n.node_scope='document' AND n.analysis_job_id=?""",
                (job["document_id"], analysis_job_id),
            )
            chapter_ids = {x["title"]: x["node_id"] for x in existing_nodes if x["node_type"] == "chapter"}
            section_ids = {(x["parent_title"], x["title"]): x["node_id"] for x in existing_nodes if x["node_type"] == "section"}
            point_ids: dict[str, str] = {}
            order = max([int(x["sort_order"]) for x in existing_nodes] or [0])
            previous = batches[resume_from - 1][-3:] if resume_from else []
            for batch_index in range(resume_from, total_batches):
                fresh = self.db.fetch_one(
                    "SELECT status FROM semantic_analysis_jobs WHERE analysis_job_id=?", (analysis_job_id,)
                )
                if not fresh or fresh["status"] == "cancelled":
                    return
                batch = batches[batch_index]
                self.db.execute(
                    """UPDATE semantic_analysis_jobs SET current_stage=?,updated_at=CURRENT_TIMESTAMP
                       WHERE analysis_job_id=?""",
                    (f"classification_batch_{batch_index + 1}", analysis_job_id),
                )
                try:
                    result = self.semantic.analyze_document_batch(
                        batch, previous, on_call=lambda: self._analysis_call(analysis_job_id)
                    )
                except Exception as batch_error:
                    raise ValidationError(
                        f"第 {batch_index + 1} 批 AI 结构分析失败，已保留上一版草稿：{str(batch_error)[:500]}"
                    ) from batch_error
                known = {row["block_id"]: row for row in batch}
                with self.db.connect() as conn:
                    for item in result.get("classifications", []):
                        block_id = str(item.get("block_id") or "")
                        if block_id not in known:
                            continue
                        destination = str(item.get("destination") or "unclassified")
                        if destination not in {"knowledge", "question_bank", "excluded"}:
                            destination = "unclassified"
                        if destination == "knowledge" and known[block_id]["block_type"] in {"image", "table"}:
                            destination = "excluded"
                        role = str(item.get("semantic_role") or "")[:64]
                        group = str(item.get("question_group_key") or "")[:100]
                        if destination == "question_bank" and not group:
                            group = f"page-{known[block_id]['page_number'] or 1}-item-{known[block_id]['block_order']}"
                        try:
                            confidence = max(0.0, min(1.0, float(item.get("confidence", 0))))
                        except (TypeError, ValueError):
                            confidence = None
                        conn.execute(
                            """UPDATE document_blocks SET content_destination=?,semantic_role=?,question_group_key=?,
                               analysis_confidence=?,analysis_reason=?,verification_status=?,updated_at=CURRENT_TIMESTAMP
                               WHERE block_id=?""",
                            (destination, role, group, confidence, str(item.get("reason") or "")[:500],
                             "rejected" if destination == "excluded" else "review_required", block_id),
                        )
                    for point in ([] if has_explicit_outline else result.get("knowledge_points", [])):
                        source_ids = [str(x) for x in point.get("block_ids", []) if str(x) in known]
                        if not source_ids:
                            continue
                        chapter = self._clean_title(point.get("chapter"), "未分章")
                        section = self._clean_title(point.get("section"), "未分节")
                        if chapter not in chapter_ids:
                            chapter_ids[chapter] = f"kn_{uuid.uuid4().hex}"
                            order += 1
                            conn.execute(
                                """INSERT INTO knowledge_nodes(node_id,course_id,document_id,node_scope,node_type,title,sort_order,analysis_job_id)
                                   VALUES(?,?,?,'document','chapter',?,?,?)""",
                                (chapter_ids[chapter], job["course_id"], job["document_id"], chapter, order, analysis_job_id),
                            )
                        section_key = (chapter, section)
                        if section_key not in section_ids:
                            section_ids[section_key] = f"kn_{uuid.uuid4().hex}"
                            order += 1
                            conn.execute(
                                """INSERT INTO knowledge_nodes(node_id,course_id,document_id,node_scope,parent_id,node_type,title,sort_order,analysis_job_id)
                                   VALUES(?,?,?,'document',?,'section',?,?,?)""",
                                (section_ids[section_key], job["course_id"], job["document_id"], chapter_ids[chapter], section, order, analysis_job_id),
                            )
                        point_key = str(point.get("point_key") or f"p-{batch_index}-{len(point_ids)}")
                        node_id = f"kn_{uuid.uuid4().hex}"
                        point_ids[point_key] = node_id
                        pages = sorted({int(known[x]["page_number"] or 1) for x in source_ids})
                        order += 1
                        conn.execute(
                            """INSERT INTO knowledge_nodes(node_id,course_id,document_id,node_scope,parent_id,node_type,title,
                               summary,markdown,keywords_json,source_pages_json,sort_order,analysis_job_id)
                               VALUES(?,?,?,'document',?,'knowledge_point',?,?,?,?,?,?,?)""",
                            (node_id, job["course_id"], job["document_id"], section_ids[section_key],
                             self._clean_title(point.get("title"), "知识点"), str(point.get("summary") or "")[:1000],
                             "\n\n".join(
                                 str(known[x].get("markdown") or known[x].get("latex") or known[x].get("plain_text") or "").strip()
                                 for x in source_ids
                                 if str(known[x].get("markdown") or known[x].get("latex") or known[x].get("plain_text") or "").strip()
                             ), json.dumps(point.get("keywords") or [], ensure_ascii=False),
                             json.dumps(pages), order, analysis_job_id),
                        )
                        for block_id in source_ids:
                            row = known[block_id]
                            conn.execute(
                                """INSERT OR IGNORE INTO knowledge_node_sources(node_id,block_id,document_id,page_number,bbox_json)
                                   VALUES(?,?,?,?,?)""",
                                (node_id, block_id, job["document_id"], row["page_number"], row["bbox_json"]),
                            )
                previous = batch[-3:]
                self.db.execute(
                    """UPDATE semantic_analysis_jobs SET current_batch=?,updated_at=CURRENT_TIMESTAMP
                       WHERE analysis_job_id=?""", (batch_index + 1, analysis_job_id),
                )
            if has_explicit_outline:
                with self.db.connect() as conn:
                    conn.execute(
                        "DELETE FROM knowledge_nodes WHERE analysis_job_id=? AND node_scope='document'",
                        (analysis_job_id,),
                    )
                self._build_faithful_document_outline(job, analysis_job_id)
            created_nodes = self.db.fetch_one(
                "SELECT COUNT(*) n FROM knowledge_nodes WHERE analysis_job_id=? AND node_scope='document'",
                (analysis_job_id,),
            )
            if not created_nodes or int(created_nodes["n"]) == 0:
                raise ValidationError("AI 未生成任何可追溯知识结构，上一版草稿已保留")
            self._rebuild_question_drafts(job["document_id"], job["course_id"])
            with self.db.connect() as conn:
                conn.execute(
                    """DELETE FROM knowledge_nodes WHERE document_id=? AND node_scope='document'
                       AND COALESCE(analysis_job_id,'')<>?
                       AND NOT EXISTS (SELECT 1 FROM knowledge_version_nodes vn WHERE vn.node_id=knowledge_nodes.node_id)""",
                    (job["document_id"], analysis_job_id),
                )
            self.db.execute(
                "UPDATE semantic_analysis_jobs SET current_stage='course_outline' WHERE analysis_job_id=?",
                (analysis_job_id,),
            )
            self._rebuild_course_outline(job["course_id"], analysis_job_id)
            self.db.execute(
                """UPDATE semantic_analysis_jobs SET status='review_required',current_stage='teacher_review',
                   result_json=?,updated_at=CURRENT_TIMESTAMP WHERE analysis_job_id=?""",
                (json.dumps({"document_nodes": int(created_nodes["n"]), "schema_version": 1}, ensure_ascii=False),
                 analysis_job_id),
            )
        except Exception as exc:
            self.db.execute(
                """UPDATE semantic_analysis_jobs SET status='failed',current_stage='failed',error_message=?,
                   updated_at=CURRENT_TIMESTAMP WHERE analysis_job_id=?""", (str(exc)[:1200], analysis_job_id),
            )

    @staticmethod
    def _is_structured_output_error(exc: Exception) -> bool:
        message = str(exc).lower()
        if any(marker in message for marker in (
            "timeout", "timed out", "readtimeout", "connectionerror", "无法连接",
        )):
            return False
        return any(marker in message for marker in (
            "json", "格式不符合", "响应缺少", "未返回有效", "必须返回",
            "遗漏候选", "遗漏来源", "证据不能", "标题层级分析", "标题分组",
        ))

    @staticmethod
    def _block_content(block: dict[str, Any]) -> str:
        return str(
            block.get("markdown") or block.get("latex") or block.get("plain_text") or ""
        ).strip()

    def _prepare_semantic_blocks(
        self, blocks: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        semantic_blocks: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for block in blocks:
            content = self._block_content(block)
            if block.get("block_type") == "image":
                skipped.append({
                    "block_id": block["block_id"], "destination": "excluded",
                    "semantic_role": "image_skipped", "question_group_key": "",
                    "confidence": 1.0, "reason": "图片块不进入知识点分析，可在原文件预览中核对",
                })
            elif not re.sub(r"\s+", "", content):
                skipped.append({
                    "block_id": block["block_id"], "destination": "excluded",
                    "semantic_role": "empty_skipped", "question_group_key": "",
                    "confidence": 1.0, "reason": "未提取到可分析文本，已安全跳过",
                })
            else:
                semantic_blocks.append(block)
        return semantic_blocks, skipped

    def _ensure_ppt_semantic_metadata(
        self, document_id: str, blocks: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], str]:
        """Backfill old PPT DocumentIR with local title metadata.

        Older imports stored one text block per slide before Fast Inspect title
        metadata was copied into ``raw_payload_json``.  Semantic re-analysis
        must not reconnect to MinerU or another document parsing service, so a
        legacy record is upgraded from the locally stored PPTX once and the
        metadata is persisted back into DocumentIR.
        """
        document = self.db.fetch_one(
            "SELECT stored_path,original_name FROM course_documents WHERE document_id=?",
            (document_id,),
        ) or {}
        source_path = Path(str(document.get("stored_path") or ""))
        if source_path.suffix.lower() != ".pptx" and not str(
            document.get("original_name") or ""
        ).lower().endswith(".pptx"):
            return blocks, ""

        def raw_payload(block: dict[str, Any]) -> dict[str, Any]:
            value = block.get("raw")
            if isinstance(value, dict):
                return dict(value)
            try:
                value = json.loads(block.get("raw_payload_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                value = {}
            return dict(value) if isinstance(value, dict) else {}

        already_current = any(
            str(raw_payload(block).get("source_kind") or "").lower() == "pptx"
            and "is_slide_title_block" in raw_payload(block)
            for block in blocks
        )
        if already_current:
            for block in blocks:
                block["raw"] = raw_payload(block)
            return blocks, "document_ir"

        slides: list[dict[str, Any]] = []
        metadata_source = ""
        if source_path.is_file():
            try:
                # This is a local OOXML title/layout read.  It never invokes
                # MinerU, OCR, or any remote document parsing endpoint.
                slides = self.ppt_inspector.inspect(source_path)
                metadata_source = "local_ppt_title_refresh"
            except Exception:
                slides = []
        if not slides:
            persisted = self.db.fetch_all(
                """SELECT slide_index,slide_type,title,layout_kind FROM presentation_slides
                   WHERE document_id=? ORDER BY slide_index""",
                (document_id,),
            )
            slides = [{
                "slide_index": int(row.get("slide_index") or 0),
                "slide_number": int(row.get("slide_index") or 0) + 1,
                "slide_type": str(row.get("slide_type") or "SIMPLE_CONTENT"),
                "title": str(row.get("title") or ""),
                "layout_kind": str(row.get("layout_kind") or "single_column"),
            } for row in persisted]
            metadata_source = "persisted_ppt_inspection" if slides else ""
        if not slides:
            return blocks, ""

        slide_meta: dict[int, dict[str, Any]] = {}
        active_title = ""
        for slide in slides:
            page_number = int(
                slide.get("slide_number") or int(slide.get("slide_index") or 0) + 1
            )
            slide_type = str(slide.get("slide_type") or "SIMPLE_CONTENT")
            detected_title = re.sub(r"\s+", " ", str(slide.get("title") or "")).strip()
            inherited = False
            if slide_type in {"COVER", "ENDING"}:
                active_title = ""
            elif detected_title:
                active_title = detected_title
            elif active_title:
                inherited = True
            title = detected_title or (active_title if inherited else "")
            hint = self._ppt_title_hint(title)
            slide_meta[page_number] = {
                "source_kind": "pptx",
                "ppt_slide_index": int(slide.get("slide_index") or page_number - 1),
                "ppt_slide_number": page_number,
                "ppt_slide_title": title,
                "ppt_detected_title": detected_title,
                "ppt_title_number": hint["number"],
                "ppt_title_level_hint": int(hint["level"]),
                "ppt_title_number_ambiguous": bool(hint["ambiguous"]),
                "ppt_title_inherited": inherited,
                "ppt_layout_kind": str(slide.get("layout_kind") or "single_column"),
                "ppt_slide_type": slide_type,
                "semantic_ppt_metadata_version": 1,
            }

        first_by_page: dict[int, str] = {}
        for block in blocks:
            page_number = int(block.get("page_number") or 1)
            first_by_page.setdefault(page_number, str(block.get("block_id") or ""))

        updates: list[tuple[str, str]] = []
        navigation_block_ids: list[str] = []
        for block in blocks:
            page_number = int(block.get("page_number") or 1)
            meta = slide_meta.get(page_number)
            if not meta:
                continue
            raw = raw_payload(block)
            raw.update(meta)
            raw["is_slide_title_block"] = bool(
                meta["ppt_detected_title"]
                and str(block.get("block_id") or "") == first_by_page.get(page_number)
            )
            block["raw"] = raw
            block["raw_payload_json"] = json.dumps(raw, ensure_ascii=False)
            updates.append((block["raw_payload_json"], str(block["block_id"])))
            if str(meta["ppt_slide_type"]) in {"COVER", "ENDING"}:
                block["include_as_navigation"] = 1
                block["include_as_knowledge"] = 0
                navigation_block_ids.append(str(block["block_id"]))
        if updates:
            with self.db.connect() as conn:
                conn.executemany(
                    """UPDATE document_blocks SET raw_payload_json=?,updated_at=CURRENT_TIMESTAMP
                       WHERE block_id=?""",
                    updates,
                )
                if navigation_block_ids:
                    conn.executemany(
                        """UPDATE document_blocks SET include_as_navigation=1,
                           include_as_knowledge=0,content_destination='excluded',
                           semantic_role='navigation',analysis_reason='PPT 封面、目录或结束页不进入知识点',
                           updated_at=CURRENT_TIMESTAMP WHERE block_id=?""",
                        [(block_id,) for block_id in navigation_block_ids],
                    )
        return blocks, metadata_source

    def _repair_map_checkpoint(
        self,
        map_results: list[dict[str, Any]],
        batches: list[list[dict[str, Any]]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Normalize incomplete map checkpoints created by an older runtime."""
        repaired_results: list[dict[str, Any]] = []
        repairs: list[dict[str, Any]] = []
        valid_destinations = {"knowledge", "question_bank", "excluded", "unclassified"}
        for result_index, raw_result in enumerate(map_results[:len(batches)]):
            result = dict(raw_result) if isinstance(raw_result, dict) else {}
            batch = batches[result_index]
            known = {str(block["block_id"]): block for block in batch}
            supplied = {
                str(item.get("block_id") or ""): item
                for item in result.get("classifications") or []
                if isinstance(item, dict) and str(item.get("block_id") or "") in known
            }
            classifications: list[dict[str, Any]] = []
            changed = False
            for block_id in known:
                item = dict(supplied.get(block_id) or {})
                destination = str(item.get("destination") or "unclassified")
                if destination not in valid_destinations:
                    destination = "unclassified"
                    changed = True
                normalized = {
                    **item,
                    "block_id": block_id,
                    "destination": destination,
                    "semantic_role": str(
                        item.get("semantic_role")
                        or ("ai_omitted_teacher_review" if not item else "teacher_review")
                    ),
                    "question_group_key": str(item.get("question_group_key") or ""),
                    "confidence": item.get("confidence", 0.0),
                    "reason": str(
                        item.get("reason")
                        or ("旧断点缺少该块分类，已保留给教师复核" if not item else "旧断点字段不完整，已保留给教师复核")
                    ),
                }
                if normalized != item:
                    changed = True
                classifications.append(normalized)

            candidates = [
                dict(candidate) for candidate in result.get("candidates") or []
                if isinstance(candidate, dict) and candidate.get("block_ids")
            ]
            if not candidates:
                knowledge_blocks = [
                    known[item["block_id"]]
                    for item in classifications if item["destination"] == "knowledge"
                ]
                safe_points = self._safe_batch_result(knowledge_blocks)["knowledge_points"]
                for point_index, point in enumerate(safe_points):
                    source_ids = [
                        str(value) for value in point.get("block_ids") or []
                        if str(value) in known
                    ]
                    if not source_ids:
                        continue
                    candidates.append({
                        **point,
                        "candidate_id": f"checkpoint-{result_index + 1}-point-{point_index + 1}",
                        "block_ids": source_ids,
                        "pages": sorted({
                            int(known[value].get("page_number") or 1) for value in source_ids
                        }),
                    })
                if candidates:
                    changed = True

            result.update({
                "batch": int(result.get("batch") or result_index + 1),
                "classifications": classifications,
                "candidates": candidates,
            })
            repaired_results.append(result)
            if changed:
                repairs.append({
                    "batch": result_index + 1,
                    "reason": "旧断点缺少语义字段或知识点数组，已从落库原文安全修复",
                })
        return repaired_results, repairs

    def _safe_batch_result(self, blocks: list[dict[str, Any]]) -> dict[str, Any]:
        """Conservative original-text fallback when the model returns truncated JSON."""
        classifications: list[dict[str, Any]] = []
        points: list[dict[str, Any]] = []
        current_title = ""
        current_ids: list[str] = []
        current_text: list[str] = []

        def flush() -> None:
            nonlocal current_ids, current_text
            if not current_ids:
                return
            joined = "\n".join(current_text).strip()
            title = current_title or re.sub(r"\s+", " ", joined)[:60] or "待教师命名知识点"
            points.append({
                "point_key": f"fallback-{len(points) + 1}",
                "chapter": "待教师整理", "section": "原文安全降级",
                "title": title, "keywords": [], "block_ids": list(current_ids),
                "evidence_quotes": [joined[:100]] if joined else [],
            })
            current_ids, current_text = [], []

        for block in blocks:
            content = self._block_content(block)
            is_question = bool(re.search(
                r"(选择题|填空题|判断题|参考答案|答案\s*[:：]|答案解析|例题\s*\d*)",
                content,
            ))
            destination = "question_bank" if is_question else "knowledge"
            classifications.append({
                "block_id": block["block_id"], "destination": destination,
                "semantic_role": "safe_fallback", "question_group_key": "",
                "confidence": 0.35,
                "reason": "模型结构化输出被截断，按原文保守归类并交教师复核",
            })
            if destination != "knowledge":
                flush()
                continue
            if block.get("block_type") == "title":
                heading = re.sub(r"^#{1,6}\s*", "", content).strip()[:180]
                if is_outline_field_heading(heading) and is_outline_unit_heading(current_title):
                    current_ids.append(str(block["block_id"]))
                    current_text.append(content)
                    continue
                flush()
                current_title = heading
            current_ids.append(str(block["block_id"]))
            current_text.append(content)
            if len(current_ids) >= 5 or sum(len(value) for value in current_text) >= 2400:
                flush()
        flush()
        return {"classifications": classifications, "knowledge_points": points}

    def _analyze_syllabus_batch_strict(
        self,
        blocks: list[dict[str, Any]],
        previous: list[dict[str, Any]],
        analysis_job_id: str,
    ) -> dict[str, Any]:
        """Analyze a syllabus batch without substituting rule-based content."""
        try:
            return self.semantic.analyze_document_batch(
                blocks, previous,
                on_call=lambda: self._analysis_call(analysis_job_id),
            )
        except Exception as exc:
            if not self._is_structured_output_error(exc):
                raise
            if len(blocks) <= 1:
                page = int((blocks[0] if blocks else {}).get("page_number") or 1)
                raise ValidationError(
                    f"教学大纲第 {page} 页的模型结构化 JSON 仍不完整；"
                    "本次分析已停止且未采用安全降级，请重试"
                ) from exc
            midpoint = max(1, len(blocks) // 2)
            left = blocks[:midpoint]
            right = blocks[midpoint:]
            left_result = self._analyze_syllabus_batch_strict(
                left, previous, analysis_job_id,
            )
            right_result = self._analyze_syllabus_batch_strict(
                right, (previous + left)[-3:], analysis_job_id,
            )
            return {
                "classifications": [
                    *left_result.get("classifications", []),
                    *right_result.get("classifications", []),
                ],
                "knowledge_points": [
                    *left_result.get("knowledge_points", []),
                    *right_result.get("knowledge_points", []),
                ],
            }

    @staticmethod
    def _fallback_reduced_points(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{
            "point_key": str(candidate.get("point_key") or candidate["candidate_id"]),
            "chapter": str(candidate.get("chapter") or "待教师整理"),
            "section": str(candidate.get("section") or "原文安全降级"),
            "title": str(candidate.get("title") or "待教师命名知识点"),
            "keywords": list(candidate.get("keywords") or []),
            "block_ids": list(candidate.get("block_ids") or []),
            "evidence_quotes": list(candidate.get("evidence_quotes") or []),
        } for candidate in candidates if candidate.get("block_ids")]

    def _sync_semantic_points_to_candidates(
        self,
        document_id: str,
        course_id: str,
        points: list[dict[str, Any]],
        known_blocks: dict[str, dict[str, Any]],
    ) -> None:
        """Mirror the latest semantic points into the pending review queue.

        Teacher decisions are durable: approved, modified, and rejected rows
        are retained. Only pending rows are replaced by the newest
        evidence-backed semantic boundaries.
        """
        protected = self.db.fetch_all(
            """SELECT * FROM knowledge_candidates WHERE document_id=?
               AND review_status IN ('APPROVED','MODIFIED','REJECTED')""",
            (document_id,),
        )
        protected_source_ids = {
            block_id
            for candidate in protected
            for block_id in self._candidate_source_ids(candidate)
        }
        prepared: list[dict[str, Any]] = []
        for point in points:
            source_ids = list(dict.fromkeys(
                str(value) for value in point.get("block_ids") or []
                if str(value) in known_blocks and str(value) not in protected_source_ids
            ))
            if not source_ids:
                continue
            seed = f"{document_id}|{'|'.join(source_ids)}"
            pages = sorted({
                int(known_blocks[value].get("page_number") or 1) for value in source_ids
            })
            bboxes: list[Any] = []
            for value in source_ids:
                bbox = known_blocks[value].get("bbox_json") or "[]"
                if isinstance(bbox, str):
                    try:
                        bbox = json.loads(bbox)
                    except json.JSONDecodeError:
                        bbox = []
                if bbox:
                    bboxes.append(bbox)
            chapter_path = list(dict.fromkeys(
                str(value).strip()
                for value in (point.get("chapter"), point.get("section"))
                if str(value or "").strip()
            ))
            markdown = "\n\n".join(
                self._block_content(known_blocks[value])
                for value in source_ids
                if self._block_content(known_blocks[value])
            )
            prepared.append({
                "candidate_id": f"kc_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:24]}",
                "title": self._clean_title(point.get("title"), "待命名知识点"),
                "source_ids": source_ids,
                "pages": pages,
                "bboxes": bboxes,
                "chapter_path": chapter_path,
                "markdown": markdown,
            })

        with self.db.connect() as conn:
            pending_ids = [
                row["candidate_id"] for row in conn.execute(
                    """SELECT candidate_id FROM knowledge_candidates WHERE document_id=?
                       AND review_status IN ('PENDING','NEEDS_REVIEW')""",
                    (document_id,),
                ).fetchall()
            ]
            if pending_ids:
                placeholders = ",".join("?" for _ in pending_ids)
                conn.execute(
                    f"DELETE FROM knowledge_candidate_blocks WHERE candidate_id IN ({placeholders})",
                    tuple(pending_ids),
                )
                conn.execute(
                    f"DELETE FROM knowledge_candidates WHERE candidate_id IN ({placeholders})",
                    tuple(pending_ids),
                )
            for candidate in prepared:
                conn.execute(
                    """INSERT INTO knowledge_candidates(
                           candidate_id,course_id,document_id,title,knowledge_type,
                           source_block_ids_json,page_start,page_end,bbox_json,
                           markdown_content,confidence,region_type,chapter_path_json,review_status)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING')""",
                    (
                        candidate["candidate_id"], course_id, document_id,
                        candidate["title"], "concept",
                        json.dumps(candidate["source_ids"], ensure_ascii=False),
                        candidate["pages"][0], candidate["pages"][-1],
                        json.dumps(candidate["bboxes"], ensure_ascii=False),
                        candidate["markdown"], 0.9, "knowledge",
                        json.dumps(candidate["chapter_path"], ensure_ascii=False),
                    ),
                )
                conn.executemany(
                    """INSERT INTO knowledge_candidate_blocks(candidate_id,block_id,sort_order)
                       VALUES(?,?,?)""",
                    [
                        (candidate["candidate_id"], block_id, order)
                        for order, block_id in enumerate(candidate["source_ids"])
                    ],
                )
            conn.execute(
                "UPDATE document_blocks SET knowledge_candidate=0 WHERE document_id=?",
                (document_id,),
            )
            conn.execute(
                """UPDATE document_blocks SET knowledge_candidate=1 WHERE block_id IN (
                       SELECT cb.block_id FROM knowledge_candidate_blocks cb
                       JOIN knowledge_candidates k USING(candidate_id) WHERE k.document_id=?
                   )""",
                (document_id,),
            )

    @staticmethod
    def _evidence_batches(blocks: list[dict[str, Any]], *, max_tokens: int = 1800,
                          max_blocks: int = 20) -> list[list[dict[str, Any]]]:
        def pack(segments: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
            packed: list[list[dict[str, Any]]] = []
            current: list[dict[str, Any]] = []
            token_estimate = 0
            for segment in segments:
                segment_tokens = sum(max(1, (len(str(
                    block.get("markdown") or block.get("latex") or block.get("plain_text") or ""
                )) + 3) // 4) for block in segment)
                if current and (
                    len(current) + len(segment) > max_blocks
                    or token_estimate + segment_tokens > max_tokens
                ):
                    packed.append(current)
                    current, token_estimate = [], 0
                current.extend(segment)
                token_estimate += segment_tokens
            if current:
                packed.append(current)
            return packed

        # A syllabus commonly repeats “教学内容 / 目标与要求” beneath each
        # experiment. Keep the whole experiment as an atomic map segment so a
        # batch boundary can never turn a field label into a standalone point.
        if any(
            block.get("block_type") == "title"
            and is_outline_unit_heading(IngestionService._block_content(block))
            for block in blocks
        ):
            segments: list[list[dict[str, Any]]] = []
            segment: list[dict[str, Any]] = []
            for block in blocks:
                if (
                    segment
                    and block.get("block_type") == "title"
                    and is_outline_unit_heading(IngestionService._block_content(block))
                ):
                    segments.append(segment)
                    segment = []
                segment.append(block)
            if segment:
                segments.append(segment)
            return pack(segments)

        batches: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        token_estimate = 0
        for block in blocks:
            content = str(block.get("markdown") or block.get("latex") or block.get("plain_text") or "")
            block_tokens = max(1, (len(content) + 3) // 4)
            if current and (len(current) >= max_blocks or token_estimate + block_tokens > max_tokens):
                batches.append(current)
                current, token_estimate = [], 0
            current.append(block)
            token_estimate += block_tokens
        if current:
            batches.append(current)
        return batches

    def _numbered_secondary_points(
        self, document_id: str, blocks: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """Build one deterministic knowledge point for every ``x.x`` section."""
        candidates = self.boundary_extractor.extract_numbered_sections(blocks, document_id)
        points: list[dict[str, Any]] = []
        covered: set[str] = set()
        for index, candidate in enumerate(candidates, 1):
            source_ids = [str(value) for value in candidate.get("source_block_ids") or []]
            if not source_ids:
                continue
            covered.update(source_ids)
            path = [str(value).strip() for value in candidate.get("chapter_path") or [] if str(value).strip()]
            title = self._clean_title(candidate.get("title"), f"第 {index} 节")
            number = re.match(r"^(\d+)\.(\d+)(?!\.\d)", title)
            chapter = path[-2] if len(path) >= 2 else (f"第 {number.group(1)} 章" if number else "未分章")
            keyword = re.sub(r"^\d+\.\d+(?!\.\d)[.)、：:\s]*", "", title).strip()
            points.append({
                "point_key": str(candidate.get("candidate_id") or f"section-{index}"),
                "chapter": chapter, "section": title, "title": title,
                "keywords": [keyword] if keyword else [], "block_ids": source_ids,
                "evidence_quotes": [title[:80]],
            })
        return points, covered

    def _syllabus_experiment_points(
        self, blocks: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """Lock every explicit syllabus experiment to one complete source unit."""
        points: list[dict[str, Any]] = []
        covered: set[str] = set()
        chapter = "实验教学内容纲要"
        for block in blocks:
            content = re.sub(r"^#{1,6}\s*", "", self._block_content(block)).strip()
            if re.match(r"^(?:四|4)[、.．]\s*实验教学内容", content):
                chapter = content
                break
        starts = [
            index for index, block in enumerate(blocks)
            if block.get("block_type") == "title"
            and is_outline_unit_heading(self._block_content(block))
            and re.match(
                r"^\s*#{0,6}\s*实验\s*(?:第\s*)?[0-9一二三四五六七八九十百千万]+",
                self._block_content(block), re.I,
            )
        ]
        for position, start in enumerate(starts):
            limit = starts[position + 1] if position + 1 < len(starts) else len(blocks)
            segment: list[dict[str, Any]] = []
            for block in blocks[start:limit]:
                content = re.sub(r"^#{1,6}\s*", "", self._block_content(block)).strip()
                if segment and block.get("block_type") == "title" and re.match(
                    r"^[一二三四五六七八九十百千万0-9]+[、.．]\s*", content
                ):
                    break
                segment.append(block)
                if re.search(r"(?:^|\n)\s*(?:五[、.．]\s*)?课程考核大纲\s*$", content):
                    break
            source_ids = [str(block["block_id"]) for block in segment]
            if not source_ids:
                continue
            heading = re.sub(r"^#{1,6}\s*", "", self._block_content(segment[0])).strip()
            unit_match = re.match(
                r"^(实验\s*(?:第\s*)?[0-9一二三四五六七八九十百千万]+)", heading
            )
            section = re.sub(r"\s+", "", unit_match.group(1)) if unit_match else heading
            points.append({
                "point_key": f"syllabus-experiment-{position + 1}",
                "chapter": chapter,
                "section": section,
                "title": heading,
                "keywords": [],
                "block_ids": source_ids,
                "evidence_quotes": [heading[:100]],
            })
            covered.update(source_ids)
        return points, covered

    def _presentation_title_points(
        self, document_id: str, blocks: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """Lock PPT knowledge boundaries to consecutive title-bar groups."""
        candidates = self.boundary_extractor.extract_presentation_title_groups(blocks, document_id)
        if not candidates:
            return [], set()
        block_map = {str(block["block_id"]): block for block in blocks}
        points: list[dict[str, Any]] = []
        covered: set[str] = set()
        current_chapter = "PPT 课程结构"
        current_section = "PPT 标题序列"
        for index, candidate in enumerate(candidates, 1):
            source_ids = [
                str(value) for value in candidate.get("source_block_ids") or []
                if str(value) in block_map
            ]
            if not source_ids:
                continue
            covered.update(source_ids)
            title = self._clean_title(candidate.get("title"), f"第 {index} 个标题")
            hint = self._ppt_title_hint(title)
            number = str(hint.get("number") or "")
            if not bool(hint.get("ambiguous")) and re.fullmatch(r"\d+(?:\.\d+)+", number):
                parts = number.split(".")
                current_chapter = f"第 {parts[0]} 章"
                current_section = title if len(parts) == 2 else ".".join(parts[:2])
            elif int(hint.get("level") or 2) == 1:
                current_chapter = title
                current_section = "PPT 标题序列"
            excerpt = "\n".join(
                self._block_content(block_map[block_id])
                for block_id in source_ids
            )[:1000]
            points.append({
                "point_key": str(candidate.get("candidate_id") or f"ppt-title-{index}"),
                "chapter": current_chapter, "section": current_section,
                "title": title, "keywords": [], "block_ids": source_ids,
                "evidence_quotes": [title[:80]],
                "pages": sorted({int(block_map[value].get("page_number") or 1) for value in source_ids}),
                "title_number": number,
                "title_level_hint": int(hint.get("level") or 2),
                "ambiguous_numbering": bool(hint.get("ambiguous")),
                "evidence_excerpt": excerpt,
            })
        return points, covered

    def _process_evidence_tree_analysis(self, job: dict[str, Any],
                                        blocks: list[dict[str, Any]]) -> None:
        analysis_job_id = str(job["analysis_job_id"])
        strict_syllabus = self._document_material_type(str(job["document_id"])) == "syllabus"
        semantic_blocks, skipped_classifications = self._prepare_semantic_blocks(blocks)
        syllabus_points, syllabus_covered = (
            self._syllabus_experiment_points(semantic_blocks)
            if strict_syllabus else ([], set())
        )
        model_blocks = [
            block for block in semantic_blocks
            if str(block["block_id"]) not in syllabus_covered
        ]
        try:
            checkpoint = json.loads(job.get("result_json") or "{}")
        except json.JSONDecodeError:
            checkpoint = {}
        if strict_syllabus and (
            checkpoint.get("fallback_batches")
            or checkpoint.get("document_reduce_fallback")
            or checkpoint.get("course_reduce_fallback")
            or any(bool(item.get("fallback")) for item in checkpoint.get("map_results", [])
                   if isinstance(item, dict))
        ):
            checkpoint = {}
        presentation_points, presentation_covered = self._presentation_title_points(
            job["document_id"], semantic_blocks
        )
        if presentation_points:
            self.db.execute(
                """UPDATE semantic_analysis_jobs SET status='running',current_stage='ppt_title_hierarchy',
                   current_batch=0,total_batches=2,error_message='',analyzer_version='ppt-title-outline-v1',
                   prompt_version='teacher-ppt-title-v1',updated_at=CURRENT_TIMESTAMP
                   WHERE analysis_job_id=?""",
                (analysis_job_id,),
            )
            try:
                presentation_points = self.semantic.resolve_presentation_outline(
                    presentation_points, on_call=lambda: self._analysis_call(analysis_job_id)
                )
            except Exception as exc:
                if not self._is_structured_output_error(exc):
                    raise
                checkpoint["presentation_outline_fallback"] = (
                    "PPT 标题层级 JSON 被截断；标题分组与页序已保留，歧义层级待教师审核"
                )
        numbered_points: list[dict[str, Any]] = []
        numbered_covered: set[str] = set()
        if not presentation_points:
            numbered_points, numbered_covered = self._numbered_secondary_points(
                job["document_id"], semantic_blocks
            )
        fixed_points = presentation_points or numbered_points
        fixed_covered = presentation_covered if presentation_points else numbered_covered
        batches = [] if fixed_points else self._evidence_batches(model_blocks)
        planned_total = 2 if presentation_points else 1 if numbered_points else len(batches) + 2
        if fixed_points:
            fixed_classifications = [{
                "block_id": str(block["block_id"]),
                "destination": "knowledge"
                if str(block["block_id"]) in fixed_covered
                else ("unclassified" if presentation_points else "excluded"),
                "semantic_role": (
                    "ppt_title_group" if presentation_points else "numbered_secondary_section"
                ) if str(block["block_id"]) in fixed_covered else "outline_container",
                "question_group_key": "",
                "confidence": 1.0 if str(block["block_id"]) in fixed_covered or not presentation_points else 0.4,
                "reason": (
                    "按连续 PPT 标题栏合并为一个待审核知识点"
                    if presentation_points else "按 x.x 二级标题合并为一个待审核知识块"
                ) if str(block["block_id"]) in fixed_covered else (
                    "未进入标题知识组，保留给教师复核"
                    if presentation_points else "x.x 范围外的章标题或前言不单独生成知识块"
                ),
            } for block in semantic_blocks]
            checkpoint.update({
                "schema_version": 6 if presentation_points else 5,
                "extractor": "ppt-title-groups" if presentation_points else "numbered-secondary-heading",
                "grouping_policy": "consecutive-same-title" if presentation_points else "one-knowledge-point-per-x.x-heading",
                "map_batch_count": 0,
                "map_results": [{
                    "batch": 0,
                    "classifications": fixed_classifications,
                    "candidates": [{
                        **point,
                        "candidate_id": str(point["point_key"]),
                        "pages": sorted({
                            int(block.get("page_number") or 1)
                            for block in semantic_blocks
                            if str(block["block_id"]) in set(point["block_ids"])
                        }),
                    } for point in fixed_points],
                    "fallback": False,
                }],
                "presentation_group_count": len(presentation_points),
                "numbered_section_count": len(numbered_points),
                "skipped_blocks": len(skipped_classifications),
                "fallback_batches": [],
            })
        elif checkpoint.get("schema_version") != 5 or checkpoint.get("map_batch_count") != len(batches):
            checkpoint = {
                "schema_version": 5, "extractor": "evidence-map-reduce",
                "map_batch_count": len(batches), "map_results": [],
                "skipped_blocks": len(skipped_classifications), "fallback_batches": [],
            }
        map_results = list(checkpoint.get("map_results") or [])
        if not fixed_points and map_results:
            map_results, checkpoint_repairs = self._repair_map_checkpoint(map_results, batches)
            checkpoint["map_results"] = map_results
            if checkpoint_repairs:
                checkpoint["checkpoint_repairs"] = checkpoint_repairs
        if job.get("ppt_metadata_source"):
            checkpoint["ppt_metadata_source"] = str(job["ppt_metadata_source"])
        completed_maps = 0 if fixed_points else min(len(map_results), len(batches))
        analyzer_version = (
            "ppt-title-outline-v1" if presentation_points else
            "numbered-secondary-outline-v1" if numbered_points else "evidence-map-reduce-v5"
        )
        initial_stage = (
            "ppt_title_grouping" if presentation_points else
            "numbered_secondary_grouping" if numbered_points else "provider_preflight"
        )
        prompt_version = "teacher-ppt-title-v1" if presentation_points else "teacher-knowledge-v5"
        self.db.execute(
            """UPDATE semantic_analysis_jobs SET status='running',current_stage=?,
               current_batch=?,total_batches=?,error_message='',analyzer_version=?,
               prompt_version=?,result_json=?,updated_at=CURRENT_TIMESTAMP
               WHERE analysis_job_id=?""",
            (initial_stage, completed_maps, planned_total, analyzer_version,
             prompt_version, json.dumps(checkpoint, ensure_ascii=False), analysis_job_id),
        )
        previous: list[dict[str, Any]] = batches[completed_maps - 1][-3:] if completed_maps else []
        for batch_index in range(completed_maps, len(batches)):
            state = self.db.fetch_one(
                "SELECT status FROM semantic_analysis_jobs WHERE analysis_job_id=?", (analysis_job_id,)
            )
            if not state or state["status"] == "cancelled":
                return
            self.db.execute(
                "UPDATE semantic_analysis_jobs SET current_stage=? WHERE analysis_job_id=?",
                (f"map_{batch_index + 1}_of_{len(batches)}", analysis_job_id),
            )
            try:
                if strict_syllabus:
                    result = self._analyze_syllabus_batch_strict(
                        batches[batch_index], previous, analysis_job_id,
                    )
                else:
                    result = self.semantic.analyze_document_batch(
                        batches[batch_index], previous,
                        on_call=lambda: self._analysis_call(analysis_job_id),
                    )
                used_fallback = False
            except Exception as exc:
                if strict_syllabus or not self._is_structured_output_error(exc):
                    raise
                result = self._safe_batch_result(batches[batch_index])
                used_fallback = True
                checkpoint.setdefault("fallback_batches", []).append({
                    "batch": batch_index + 1,
                    "reason": "模型结构化 JSON 被截断，已按原文安全降级",
                })
            known = {str(row["block_id"]): row for row in batches[batch_index]}
            candidates: list[dict[str, Any]] = []
            for point_index, point in enumerate(result.get("knowledge_points", [])):
                source_ids = [str(value) for value in point["block_ids"] if str(value) in known]
                candidates.append({
                    **point,
                    "candidate_id": f"map-{batch_index + 1}-point-{point_index + 1}",
                    "block_ids": source_ids,
                    "pages": sorted({int(known[value].get("page_number") or 1) for value in source_ids}),
                })
            map_results.append({
                "batch": batch_index + 1,
                "classifications": result.get("classifications", []),
                "candidates": candidates,
                "fallback": used_fallback,
            })
            checkpoint["map_results"] = map_results
            self.db.execute(
                """UPDATE semantic_analysis_jobs SET current_batch=?,result_json=?,updated_at=CURRENT_TIMESTAMP
                   WHERE analysis_job_id=?""",
                (batch_index + 1, json.dumps(checkpoint, ensure_ascii=False), analysis_job_id),
            )
            previous = batches[batch_index][-3:]

        candidates = [candidate for result in map_results for candidate in result.get("candidates", [])]
        classifications = skipped_classifications + [
            item for result in map_results for item in result.get("classifications", [])
        ]
        if syllabus_points:
            classifications.extend({
                "block_id": block_id,
                "destination": "knowledge",
                "semantic_role": "syllabus_experiment_source",
                "question_group_key": "",
                "confidence": 1.0,
                "reason": "教学大纲实验标题及其所属原文必须完整保留",
            } for block_id in syllabus_covered)
            checkpoint["syllabus_experiment_count"] = len(syllabus_points)
        if not candidates and not syllabus_points:
            with self.db.connect() as conn:
                for item in classifications:
                    if not isinstance(item, dict) or not str(item.get("block_id") or ""):
                        continue
                    destination = str(item.get("destination") or "unclassified")
                    if destination not in {"knowledge", "question_bank", "excluded", "unclassified"}:
                        destination = "unclassified"
                    try:
                        confidence = max(0.0, min(1.0, float(item.get("confidence", 0))))
                    except (TypeError, ValueError):
                        confidence = 0.0
                    conn.execute(
                        """UPDATE document_blocks SET content_destination=?,semantic_role=?,
                           analysis_confidence=?,analysis_reason=?,verification_status=?,
                           updated_at=CURRENT_TIMESTAMP WHERE block_id=?""",
                        (destination, str(item.get("semantic_role") or "teacher_review")[:64], confidence,
                         str(item.get("reason") or "AI 未提供分类理由")[:500],
                         "rejected" if destination == "excluded" else "review_required",
                         str(item["block_id"])),
                    )
            checkpoint.update({
                "document_nodes": 0, "completed": True,
                "notice": "资料仅包含图片、空白内容或题目，未生成知识点",
            })
            self.db.execute(
                """UPDATE semantic_analysis_jobs SET status='review_required',
                   current_stage='teacher_review',current_batch=?,total_batches=?,
                   result_json=?,retry_count=0,next_retry_at=NULL,error_message=?,
                   updated_at=CURRENT_TIMESTAMP WHERE analysis_job_id=?""",
                (planned_total, planned_total, json.dumps(checkpoint, ensure_ascii=False),
                 "未提取到可生成知识点的正文；图片和空白块已跳过", analysis_job_id),
            )
            return
        if fixed_points:
            points = fixed_points
        elif not candidates:
            points = list(syllabus_points)
        else:
            self.db.execute(
                "UPDATE semantic_analysis_jobs SET current_stage='document_reduce' WHERE analysis_job_id=?",
                (analysis_job_id,),
            )
            try:
                reduced = self.semantic.reduce_document_outline(
                    candidates, on_call=lambda: self._analysis_call(analysis_job_id)
                )
                points = reduced["knowledge_points"]
            except Exception as exc:
                if strict_syllabus and self._is_structured_output_error(exc):
                    try:
                        reduced = self.semantic.reduce_document_outline(
                            candidates, on_call=lambda: self._analysis_call(analysis_job_id)
                        )
                        points = reduced["knowledge_points"]
                    except Exception as retry_exc:
                        raise ValidationError(
                            "教学大纲文档归并的模型 JSON 仍不完整；"
                            "本次分析已停止且未采用安全降级，请重试"
                        ) from retry_exc
                elif not self._is_structured_output_error(exc):
                    raise
                else:
                    points = self._fallback_reduced_points(candidates)
                    checkpoint["document_reduce_fallback"] = (
                        "文档归并 JSON 被截断，保留分批原文结构供教师审核"
                    )
            points.extend(syllabus_points)
        checkpoint["document_points"] = points
        self.db.execute(
            """UPDATE semantic_analysis_jobs SET current_batch=?,result_json=?,updated_at=CURRENT_TIMESTAMP
               WHERE analysis_job_id=?""",
            (planned_total - 1 if fixed_points else len(batches) + 1,
             json.dumps(checkpoint, ensure_ascii=False), analysis_job_id),
        )

        known = {str(row["block_id"]): row for row in blocks}
        with self.db.connect() as conn:
            conn.execute(
                """DELETE FROM knowledge_nodes WHERE analysis_job_id=?
                   AND NOT EXISTS (SELECT 1 FROM knowledge_version_nodes vn WHERE vn.node_id=knowledge_nodes.node_id)""",
                (analysis_job_id,),
            )
            conn.execute(
                """UPDATE document_blocks SET content_destination='unclassified',semantic_role='teacher_review',
                    analysis_confidence=NULL,analysis_reason='AI 未可靠归类',question_group_key='',
                    verification_status='review_required'
                    WHERE document_id=? AND include_as_knowledge=1""",
                (job["document_id"],),
            )
            for item in classifications:
                if not isinstance(item, dict) or str(item.get("block_id") or "") not in known:
                    continue
                block_id = str(item["block_id"])
                destination = str(item.get("destination") or "unclassified")
                if destination not in {"knowledge", "question_bank", "excluded", "unclassified"}:
                    destination = "unclassified"
                if destination == "knowledge" and known[block_id]["block_type"] in {"image", "table"}:
                    destination = "excluded"
                group = str(item.get("question_group_key") or "")[:100]
                if destination == "question_bank" and not group:
                    group = f"page-{known[block_id]['page_number'] or 1}-item-{known[block_id]['block_order']}"
                try:
                    confidence = max(0.0, min(1.0, float(item.get("confidence", 0))))
                except (TypeError, ValueError):
                    confidence = None
                conn.execute(
                    """UPDATE document_blocks SET content_destination=?,semantic_role=?,question_group_key=?,
                       analysis_confidence=?,analysis_reason=?,verification_status=?,updated_at=CURRENT_TIMESTAMP
                       WHERE block_id=?""",
                    (destination, str(item.get("semantic_role") or "")[:64], group, confidence,
                     str(item.get("reason") or "")[:500],
                     "rejected" if destination == "excluded" else "review_required", block_id),
                )
            chapter_ids: dict[str, str] = {}
            section_ids: dict[tuple[str, str], str] = {}
            order = 0
            for point in points:
                source_ids = [str(value) for value in point["block_ids"] if str(value) in known]
                if not source_ids:
                    raise ValidationError(f"知识点“{point.get('title')}”没有有效来源")
                chapter = self._clean_title(point.get("chapter"), "未分章")
                section = self._clean_title(point.get("section"), "未分节")
                if chapter not in chapter_ids:
                    order += 1
                    chapter_ids[chapter] = f"kn_{uuid.uuid4().hex}"
                    conn.execute(
                        """INSERT INTO knowledge_nodes(node_id,course_id,document_id,node_scope,node_type,title,
                           summary,sort_order,analysis_job_id) VALUES(?,?,?,'document','chapter',?,'',?,?)""",
                        (chapter_ids[chapter], job["course_id"], job["document_id"], chapter, order, analysis_job_id),
                    )
                section_key = (chapter, section)
                if section_key not in section_ids:
                    order += 1
                    section_ids[section_key] = f"kn_{uuid.uuid4().hex}"
                    conn.execute(
                        """INSERT INTO knowledge_nodes(node_id,course_id,document_id,node_scope,parent_id,node_type,
                           title,summary,sort_order,analysis_job_id) VALUES(?,?,?,'document',?,'section',?,'',?,?)""",
                        (section_ids[section_key], job["course_id"], job["document_id"], chapter_ids[chapter],
                         section, order, analysis_job_id),
                    )
                order += 1
                node_id = f"kn_{uuid.uuid4().hex}"
                pages = sorted({int(known[value].get("page_number") or 1) for value in source_ids})
                markdown = "\n\n".join(
                    str(known[value].get("markdown") or known[value].get("latex")
                        or known[value].get("plain_text") or "").strip()
                    for value in source_ids
                )
                conn.execute(
                    """INSERT INTO knowledge_nodes(node_id,course_id,document_id,node_scope,parent_id,node_type,title,
                       summary,markdown,keywords_json,source_pages_json,sort_order,analysis_job_id)
                       VALUES(?,?,?,'document',?,'knowledge_point',?,'',?,?,?,?,?)""",
                    (node_id, job["course_id"], job["document_id"], section_ids[section_key],
                     self._clean_title(point.get("title"), "知识点"), markdown,
                     json.dumps(point.get("keywords") or [], ensure_ascii=False), json.dumps(pages),
                     order, analysis_job_id),
                )
                for block_id in source_ids:
                    row = known[block_id]
                    conn.execute(
                        """INSERT OR IGNORE INTO knowledge_node_sources(node_id,block_id,document_id,page_number,bbox_json)
                           VALUES(?,?,?,?,?)""",
                        (node_id, block_id, job["document_id"], row["page_number"], row["bbox_json"]),
                    )

        self._sync_semantic_points_to_candidates(
            job["document_id"], job["course_id"], points, known
        )
        self._rebuild_question_drafts(job["document_id"], job["course_id"])
        self.db.execute(
            "UPDATE semantic_analysis_jobs SET current_stage='course_reduce' WHERE analysis_job_id=?",
            (analysis_job_id,),
        )
        course_fallback = self._rebuild_course_outline(job["course_id"], analysis_job_id)
        if course_fallback:
            checkpoint["course_reduce_fallback"] = course_fallback
        with self.db.connect() as conn:
            conn.execute(
                """DELETE FROM knowledge_nodes WHERE document_id=? AND node_scope='document'
                   AND COALESCE(analysis_job_id,'')<>?
                   AND NOT EXISTS (SELECT 1 FROM knowledge_version_nodes vn WHERE vn.node_id=knowledge_nodes.node_id)""",
                (job["document_id"], analysis_job_id),
            )
        unclassified = [
            block_id for block_id, row in known.items()
            if not any(str(item.get("block_id") or "") == block_id and item.get("destination") in
                       {"knowledge", "question_bank", "excluded"} for item in classifications)
        ]
        checkpoint.update({
            "document_nodes": len(points), "unclassified_block_ids": unclassified,
            "completed": True,
        })
        self.db.execute(
            """UPDATE semantic_analysis_jobs SET status='review_required',current_stage='teacher_review',
               current_batch=?,total_batches=?,result_json=?,retry_count=0,next_retry_at=NULL,
               error_message='',updated_at=CURRENT_TIMESTAMP WHERE analysis_job_id=?""",
            (planned_total, planned_total, json.dumps(checkpoint, ensure_ascii=False), analysis_job_id),
        )

    def _process_docling_graph_analysis(
        self, job: dict[str, Any], blocks: list[dict[str, Any]]
    ) -> None:
        """Build one evidence-backed document tree through the optional Docling Graph backend."""
        analysis_job_id = job["analysis_job_id"]
        document = self.db.fetch_one(
            "SELECT original_name FROM course_documents WHERE document_id=?", (job["document_id"],)
        ) or {"original_name": "课程资料"}
        self.db.execute(
            """UPDATE semantic_analysis_jobs SET status='running',current_stage='docling_graph_extraction',
               current_batch=0,total_batches=1,error_message='',analyzer_version='docling-graph',
               prompt_version='teacher-course-tree-v2',updated_at=CURRENT_TIMESTAMP
               WHERE analysis_job_id=?""",
            (analysis_job_id,),
        )
        canonical_markdown = ""
        artifact = self.db.fetch_one(
            """SELECT stored_path FROM document_artifacts WHERE document_id=?
               AND artifact_type='canonical_markdown' AND status='ready'""",
            (job["document_id"],),
        )
        if artifact and artifact.get("stored_path"):
            artifact_path = Path(str(artifact["stored_path"]))
            if artifact_path.is_file():
                canonical_markdown = artifact_path.read_text(encoding="utf-8")
        result = self.docling_graph.analyze(
            blocks,
            document_title=str(document["original_name"]),
            canonical_markdown=canonical_markdown,
            on_call=lambda: self._analysis_call(analysis_job_id),
        )
        known = {str(row["block_id"]): row for row in blocks}
        points = result.get("knowledge_points")
        classifications = result.get("classifications")
        if not isinstance(points, list) or not points:
            raise ValidationError("Docling Graph 没有生成知识点")
        if not isinstance(classifications, list):
            raise ValidationError("Docling Graph 没有返回完整块分类")
        with self.db.connect() as conn:
            conn.execute(
                """DELETE FROM knowledge_nodes WHERE analysis_job_id=?
                   AND NOT EXISTS (SELECT 1 FROM knowledge_version_nodes vn WHERE vn.node_id=knowledge_nodes.node_id)""",
                (analysis_job_id,),
            )
            conn.execute(
                """UPDATE document_blocks SET content_destination='unclassified',semantic_role='teacher_review',
                   analysis_confidence=NULL,analysis_reason='Docling Graph 未可靠归类',question_group_key='',
                   verification_status='review_required' WHERE document_id=?""",
                (job["document_id"],),
            )
            for item in classifications:
                if not isinstance(item, dict):
                    continue
                block_id = str(item.get("block_id") or "")
                if block_id not in known:
                    continue
                destination = str(item.get("destination") or "unclassified")
                if destination not in {"knowledge", "question_bank", "excluded", "unclassified"}:
                    destination = "unclassified"
                group = str(item.get("question_group_key") or "")[:100]
                if destination == "question_bank" and not group:
                    group = f"page-{known[block_id]['page_number'] or 1}-item-{known[block_id]['block_order']}"
                conn.execute(
                    """UPDATE document_blocks SET content_destination=?,semantic_role=?,question_group_key=?,
                       analysis_confidence=NULL,analysis_reason=?,verification_status=?,updated_at=CURRENT_TIMESTAMP
                       WHERE block_id=?""",
                    (
                        destination,
                        str(item.get("semantic_role") or "")[:64],
                        group,
                        str(item.get("reason") or "")[:500],
                        "rejected" if destination == "excluded" else "review_required",
                        block_id,
                    ),
                )
            chapter_ids: dict[str, str] = {}
            section_ids: dict[tuple[str, str], str] = {}
            order = 0
            for index, point in enumerate(points):
                if not isinstance(point, dict):
                    continue
                source_ids = list(dict.fromkeys(
                    str(value) for value in point.get("block_ids", []) if str(value) in known
                ))
                if not source_ids:
                    raise ValidationError(f"第 {index + 1} 个知识点没有有效来源块")
                chapter = self._clean_title(point.get("chapter"), "未分章")
                section = self._clean_title(point.get("section"), "未分节")
                if chapter not in chapter_ids:
                    order += 1
                    chapter_ids[chapter] = f"kn_{uuid.uuid4().hex}"
                    conn.execute(
                        """INSERT INTO knowledge_nodes(node_id,course_id,document_id,node_scope,node_type,title,
                           sort_order,analysis_job_id) VALUES(?,?,?,'document','chapter',?,?,?)""",
                        (chapter_ids[chapter], job["course_id"], job["document_id"], chapter, order, analysis_job_id),
                    )
                section_key = (chapter, section)
                if section_key not in section_ids:
                    order += 1
                    section_ids[section_key] = f"kn_{uuid.uuid4().hex}"
                    conn.execute(
                        """INSERT INTO knowledge_nodes(node_id,course_id,document_id,node_scope,parent_id,node_type,
                           title,sort_order,analysis_job_id) VALUES(?,?,?,'document',?,'section',?,?,?)""",
                        (section_ids[section_key], job["course_id"], job["document_id"], chapter_ids[chapter],
                         section, order, analysis_job_id),
                    )
                node_id = f"kn_{uuid.uuid4().hex}"
                pages = sorted({int(known[value]["page_number"] or 1) for value in source_ids})
                source_markdown = "\n\n".join(
                    str(known[value].get("markdown") or known[value].get("latex")
                        or known[value].get("plain_text") or "").strip()
                    for value in source_ids
                    if str(known[value].get("markdown") or known[value].get("latex")
                           or known[value].get("plain_text") or "").strip()
                )
                order += 1
                conn.execute(
                    """INSERT INTO knowledge_nodes(node_id,course_id,document_id,node_scope,parent_id,node_type,title,
                       summary,markdown,keywords_json,source_pages_json,sort_order,analysis_job_id)
                       VALUES(?,?,?,'document',?,'knowledge_point',?,?,?,?,?,?,?)""",
                    (
                        node_id, job["course_id"], job["document_id"], section_ids[section_key],
                        self._clean_title(point.get("title"), "知识点"),
                        str(point.get("summary") or "")[:1000], source_markdown,
                        json.dumps(point.get("keywords") or [], ensure_ascii=False),
                        json.dumps(pages), order, analysis_job_id,
                    ),
                )
                for block_id in source_ids:
                    row = known[block_id]
                    conn.execute(
                        """INSERT OR IGNORE INTO knowledge_node_sources(node_id,block_id,document_id,page_number,bbox_json)
                           VALUES(?,?,?,?,?)""",
                        (node_id, block_id, job["document_id"], row["page_number"], row["bbox_json"]),
                    )
        created = self.db.fetch_one(
            "SELECT COUNT(*) n FROM knowledge_nodes WHERE analysis_job_id=? AND node_scope='document'",
            (analysis_job_id,),
        )
        if not created or int(created["n"]) == 0:
            raise ValidationError("Docling Graph 没有生成可审核的知识结构")
        self._rebuild_question_drafts(job["document_id"], job["course_id"])
        with self.db.connect() as conn:
            conn.execute(
                """DELETE FROM knowledge_nodes WHERE document_id=? AND node_scope='document'
                   AND COALESCE(analysis_job_id,'')<>?
                   AND NOT EXISTS (SELECT 1 FROM knowledge_version_nodes vn WHERE vn.node_id=knowledge_nodes.node_id)""",
                (job["document_id"], analysis_job_id),
            )
        self.db.execute(
            "UPDATE semantic_analysis_jobs SET current_stage='course_outline' WHERE analysis_job_id=?",
            (analysis_job_id,),
        )
        self._rebuild_course_outline(job["course_id"], analysis_job_id)
        payload = {
            "document_nodes": int(created["n"]),
            "schema_version": 2,
            "extractor": "docling_graph",
            "provenance_resolution": result.get("provenance_resolution", "block-marker"),
        }
        self.db.execute(
            """UPDATE semantic_analysis_jobs SET status='review_required',current_stage='teacher_review',
               current_batch=1,total_batches=1,analyzer_version=?,prompt_version=?,result_json=?,
               updated_at=CURRENT_TIMESTAMP WHERE analysis_job_id=?""",
            (
                str(result.get("analyzer_version") or "docling-graph")[:100],
                str(result.get("prompt_version") or "teacher-course-tree-v2")[:100],
                json.dumps(payload, ensure_ascii=False), analysis_job_id,
            ),
        )

    @staticmethod
    def _heading_from_line(line: str) -> tuple[int, str] | None:
        stripped = line.strip()
        markdown = re.match(r"^(#{1,6})\s+(.+?)\s*$", stripped)
        if markdown:
            return min(len(markdown.group(1)), 3), markdown.group(2).strip()
        if len(stripped) > 180:
            return None
        chapter = re.match(r"^第\s*[一二三四五六七八九十百0-9]+\s*[章篇部]\s*.*$", stripped)
        if chapter:
            return 1, stripped
        section = re.match(r"^第\s*[一二三四五六七八九十百0-9]+\s*节\s*.*$", stripped)
        if section:
            return 2, stripped
        numbered = re.match(r"^((?:\d+\.\d+)+(?:\.)?|\d+\.)[、\s]+\S.*$", stripped)
        if numbered:
            token = numbered.group(1).rstrip(".")
            return min(token.count(".") + 1, 3), stripped
        return None

    @staticmethod
    def _normalized_outline_title(value: Any) -> str:
        return re.sub(
            r"[\s·、，。,:：;；/\\_—\-（）()\[\]【】]+", "",
            str(value or "").strip().lower(),
        )

    @staticmethod
    def _chinese_outline_number(value: str) -> int | None:
        value = value.strip()
        if value.isdigit():
            return int(value)
        digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
                  "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        units = {"十": 10, "百": 100, "千": 1000}
        if not value or any(char not in digits and char not in units for char in value):
            return None
        total = 0
        current = 0
        for char in value:
            if char in digits:
                current = digits[char]
            else:
                total += (current or 1) * units[char]
                current = 0
        return total + current

    @classmethod
    def _outline_number_parts(cls, value: Any) -> tuple[int, ...]:
        title = str(value or "").strip().replace("．", ".")
        chapter = re.match(r"^第\s*([零〇一二三四五六七八九十百千0-9]+)\s*章", title)
        if chapter:
            number = cls._chinese_outline_number(chapter.group(1))
            return (number,) if number is not None else ()
        numbered = re.match(r"^(\d+(?:\.\d+)*)\s*(?=$|[.)、:：\s])", title)
        if not numbered:
            return ()
        return tuple(int(value) for value in numbered.group(1).split("."))

    @classmethod
    def _document_primary_chapter(
        cls, job: dict[str, Any], candidates: list[dict[str, Any]]
    ) -> tuple[int, str] | None:
        """Return an explicit document-level chapter anchor, never a guessed one."""
        stem = Path(str(job.get("original_name") or "")).stem
        match = re.search(
            r"第\s*([零〇一二三四五六七八九十百千0-9]+)\s*章",
            stem,
        )
        if match:
            number = cls._chinese_outline_number(match.group(1))
            if number is not None:
                label = re.sub(r"[_\-]+", " ", stem[match.start():]).strip()
                return number, label[:180]

        explicit: dict[int, str] = {}
        for candidate in candidates:
            title = str(candidate.get("title") or "").strip()
            title_match = re.match(
                r"^第\s*([零〇一二三四五六七八九十百千0-9]+)\s*章",
                title,
            )
            if not title_match:
                continue
            number = cls._chinese_outline_number(title_match.group(1))
            if number is not None:
                explicit.setdefault(number, title)
        if len(explicit) == 1:
            number, label = next(iter(explicit.items()))
            return number, label[:180]
        return None

    def _rebuild_document_outline_from_candidates(
        self,
        job: dict[str, Any],
        analysis_job_id: str | None,
        *,
        replace_existing: bool = False,
    ) -> int:
        """Build one real document knowledge point per normalized candidate title."""
        candidates = self.db.fetch_all(
            """SELECT * FROM knowledge_candidates WHERE document_id=?
               AND review_status!='REJECTED' ORDER BY page_start,page_end,candidate_id""",
            (job["document_id"],),
        )
        if not candidates:
            return 0

        grouped: list[dict[str, Any]] = []
        by_title: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            title = self._clean_title(candidate.get("title"), "待命名知识点")
            title_key = self._normalized_outline_title(title)
            entry = by_title.get(title_key)
            if entry is None:
                entry = {
                    "title": title,
                    "rows": [],
                    "page_start": int(candidate.get("page_start") or 1),
                    "page_end": int(candidate.get("page_end") or candidate.get("page_start") or 1),
                }
                by_title[title_key] = entry
                grouped.append(entry)
            entry["rows"].append(candidate)
            entry["page_start"] = min(entry["page_start"], int(candidate.get("page_start") or 1))
            entry["page_end"] = max(
                entry["page_end"],
                int(candidate.get("page_end") or candidate.get("page_start") or 1),
            )

        grouped.sort(key=lambda row: (row["page_start"], row["page_end"], row["title"]))
        primary_chapter = self._document_primary_chapter(job, candidates)
        chapter_titles: dict[int, str] = {}
        section_titles: dict[tuple[int, int], str] = {}
        for entry in grouped:
            parts = self._outline_number_parts(entry["title"])
            if len(parts) == 1:
                chapter_titles.setdefault(parts[0], entry["title"])
            elif len(parts) == 2:
                section_titles.setdefault((parts[0], parts[1]), entry["title"])

        previous_points = {
            self._normalized_outline_title(row["title"]): row
            for row in self.db.fetch_all(
                """SELECT * FROM knowledge_nodes WHERE document_id=? AND node_scope='document'
                   AND node_type='knowledge_point' AND status!='rejected'""",
                (job["document_id"],),
            )
        }
        if replace_existing:
            with self.db.connect() as conn:
                conn.execute(
                    """DELETE FROM knowledge_nodes WHERE document_id=? AND node_scope='document'
                       AND NOT EXISTS (
                           SELECT 1 FROM knowledge_version_nodes vn
                           WHERE vn.node_id=knowledge_nodes.node_id
                       )""",
                    (job["document_id"],),
                )

        material_type = self._document_material_type(str(job["document_id"]))
        chapter_ids: dict[str, str] = {}
        section_ids: dict[tuple[str, str], str] = {}
        current_chapter = "导学"
        order = 0
        created = 0
        with self.db.connect() as conn:
            for entry in grouped:
                title = str(entry["title"])
                number_parts = self._outline_number_parts(title)
                if number_parts:
                    if primary_chapter:
                        current_chapter = primary_chapter[1]
                    else:
                        chapter_number = number_parts[0]
                        current_chapter = chapter_titles.get(chapter_number) or f"第{chapter_number}章"
                chapter = current_chapter
                if len(number_parts) == 2:
                    section = title
                elif len(number_parts) >= 3:
                    section = section_titles.get(
                        (number_parts[0], number_parts[1]),
                        ".".join(str(value) for value in number_parts[:2]),
                    )
                else:
                    section = title

                if chapter not in chapter_ids:
                    order += 1
                    chapter_id = f"kn_{uuid.uuid4().hex}"
                    chapter_ids[chapter] = chapter_id
                    conn.execute(
                        """INSERT INTO knowledge_nodes(
                               node_id,course_id,document_id,node_scope,node_type,title,sort_order,
                               status,analysis_job_id,material_type,source_fingerprint
                           ) VALUES(?,?,?,'document','chapter',?,?,'draft',?,?,?)""",
                        (
                            chapter_id, job["course_id"], job["document_id"], chapter, order,
                            analysis_job_id, material_type,
                            self._outline_fingerprint(material_type, "document_chapter", [chapter]),
                        ),
                    )
                section_key = (chapter, section)
                if section_key not in section_ids:
                    order += 1
                    section_id = f"kn_{uuid.uuid4().hex}"
                    section_ids[section_key] = section_id
                    conn.execute(
                        """INSERT INTO knowledge_nodes(
                               node_id,course_id,document_id,node_scope,parent_id,node_type,title,
                               sort_order,status,analysis_job_id,material_type,source_fingerprint
                           ) VALUES(?,?,?,'document',?,'section',?,?,'draft',?,?,?)""",
                        (
                            section_id, job["course_id"], job["document_id"], chapter_ids[chapter],
                            section, order, analysis_job_id, material_type,
                            self._outline_fingerprint(
                                material_type, "document_section", [chapter, section]
                            ),
                        ),
                    )

                candidate_ids = [str(row["candidate_id"]) for row in entry["rows"]]
                placeholders = ",".join("?" for _ in candidate_ids)
                source_rows = [
                    dict(row) for row in conn.execute(
                        f"""SELECT b.* FROM knowledge_candidate_blocks cb
                            JOIN document_blocks b ON b.block_id=cb.block_id
                            WHERE cb.candidate_id IN ({placeholders})
                            ORDER BY b.page_number,b.block_order,b.block_id""",
                        candidate_ids,
                    ).fetchall()
                ]
                unique_sources = {
                    str(row["block_id"]): row for row in source_rows
                }
                source_rows = list(unique_sources.values())
                pages = sorted({
                    int(row.get("page_number") or 1) for row in source_rows
                } or set(range(int(entry["page_start"]), int(entry["page_end"]) + 1)))
                markdown_parts: list[str] = []
                for candidate in entry["rows"]:
                    content = str(
                        candidate.get("teacher_revision")
                        if candidate.get("review_status") == "MODIFIED"
                        and str(candidate.get("teacher_revision") or "").strip()
                        else candidate.get("markdown_content") or ""
                    ).strip()
                    if content and content not in markdown_parts:
                        markdown_parts.append(content)
                markdown = "\n\n".join(markdown_parts)
                approved = all(
                    row.get("review_status") in {"APPROVED", "MODIFIED"}
                    for row in entry["rows"]
                )
                prior = previous_points.get(self._normalized_outline_title(title))
                same_pages = bool(prior) and sorted(
                    json.loads(prior.get("source_pages_json") or "[]")
                ) == pages
                preserve_prior = bool(
                    prior and same_pages
                    and (prior.get("reviewed_by") or prior.get("status") == "approved")
                )
                point_title = str(prior["title"]) if preserve_prior else title
                point_markdown = str(prior["markdown"]) if preserve_prior else markdown
                point_status = str(prior["status"]) if preserve_prior else (
                    "approved" if approved else "draft"
                )
                reviewed_by = (
                    prior.get("reviewed_by") if preserve_prior else
                    next((row.get("reviewed_by") for row in entry["rows"] if row.get("reviewed_by")), None)
                )
                reviewed_at = (
                    prior.get("reviewed_at") if preserve_prior else
                    next((row.get("reviewed_at") for row in entry["rows"] if row.get("reviewed_at")), None)
                )
                order += 1
                node_id = f"kn_{uuid.uuid4().hex}"
                source_ids = [str(row["block_id"]) for row in source_rows]
                fingerprint = self._outline_fingerprint(
                    material_type, "document_knowledge_point",
                    [self._normalized_outline_title(title), *source_ids],
                )
                conn.execute(
                    """INSERT INTO knowledge_nodes(
                           node_id,course_id,document_id,node_scope,parent_id,node_type,title,
                           markdown,keywords_json,source_pages_json,sort_order,status,analysis_job_id,
                           reviewed_by,reviewed_at,material_type,source_fingerprint
                       ) VALUES(?,?,?,'document',?,'knowledge_point',?,?,'[]',?,?,?,?,?,?,?,?)""",
                    (
                        node_id, job["course_id"], job["document_id"], section_ids[section_key],
                        point_title, point_markdown, json.dumps(pages), order, point_status,
                        analysis_job_id, reviewed_by, reviewed_at, material_type, fingerprint,
                    ),
                )
                for source in source_rows:
                    conn.execute(
                        """INSERT OR IGNORE INTO knowledge_node_sources(
                               node_id,block_id,document_id,page_number,bbox_json
                           ) VALUES(?,?,?,?,?)""",
                        (
                            node_id, source["block_id"], job["document_id"],
                            source.get("page_number"), source.get("bbox_json") or "[]",
                        ),
                    )
                created += 1
        return created

    def _build_faithful_document_outline(self, job: dict[str, Any], analysis_job_id: str, *,
                                         include_unclassified: bool = False) -> None:
        if self._rebuild_document_outline_from_candidates(job, analysis_job_id):
            return
        document = self.db.fetch_one(
            "SELECT original_name FROM course_documents WHERE document_id=?", (job["document_id"],)
        ) or {"original_name": "正文"}
        blocks = self.db.fetch_all(
            """SELECT * FROM document_blocks WHERE document_id=?
                 AND (content_destination='knowledge'
                      OR (?=1 AND content_destination='unclassified'))
               ORDER BY page_number,block_order""",
            (job["document_id"], 1 if include_unclassified else 0),
        )
        units: list[dict[str, Any]] = []
        stack: dict[int, int] = {}
        active: int | None = None
        last_chapter_number: int | None = None

        def begin(level: int, title: str, block: dict[str, Any]) -> None:
            nonlocal active
            parent = stack.get(2) if level >= 3 else stack.get(1) if level == 2 else None
            if level >= 3 and parent is None:
                parent = stack.get(1)
            units.append({
                "level": level,
                "node_type": "chapter" if level == 1 else "section" if level == 2 else "knowledge_point",
                "title": title,
                "parent_index": parent,
                "lines": [str(block.get("markdown") or "").strip()]
                if block.get("block_type") == "title" else [],
                "sources": {},
            })
            active = len(units) - 1
            stack[level] = active
            for deeper in [key for key in stack if key > level]:
                stack.pop(deeper, None)
            units[active]["sources"][block["block_id"]] = block

        def append_line(line: str, block: dict[str, Any]) -> None:
            nonlocal active
            if active is None:
                units.append({
                    "level": 3,
                    "node_type": "knowledge_point",
                    "title": str(document["original_name"]),
                    "parent_index": None,
                    "lines": [],
                    "sources": {},
                })
                active = len(units) - 1
            units[active]["lines"].append(line)
            units[active]["sources"][block["block_id"]] = block

        for block in blocks:
            text = str(block.get("markdown") or block.get("latex") or block.get("plain_text") or "")
            if not text.strip():
                continue
            try:
                raw = json.loads(block.get("raw_payload_json") or "{}")
            except json.JSONDecodeError:
                raw = {}
            raw_level = int(raw.get("heading_level") or 0)
            raw_title = str(raw.get("section") or "").strip()
            if raw_level and raw_title:
                normalized_level = min(raw_level, 3)
                if active is None or units[active]["title"] != raw_title or units[active]["level"] != normalized_level:
                    begin(normalized_level, raw_title, block)
                if raw_level == 1:
                    raw_number = re.match(r"^(\d+)\.", raw_title)
                    if raw_number:
                        last_chapter_number = int(raw_number.group(1))
            lines = text.splitlines()
            if block.get("block_type") == "title" and raw_level:
                lines = []
            if block.get("block_type") == "title" and lines and not raw_level:
                detected = self._heading_from_line(lines[0])
                begin(*(detected or (1 if not stack else 2, lines[0].strip())), block)
                lines = lines[1:]
            for index, line in enumerate(lines):
                detected = self._heading_from_line(line)
                if detected:
                    if detected[0] == 1:
                        numeric_chapter = re.match(r"^(\d+)\.", detected[1])
                        if numeric_chapter:
                            chapter_number = int(numeric_chapter.group(1))
                            if last_chapter_number is not None and chapter_number <= last_chapter_number:
                                append_line(line, block)
                                continue
                            last_chapter_number = chapter_number
                    begin(detected[0], detected[1], block)
                elif line.strip():
                    if not units and index == 0 and len(lines) > 1 and line.strip() == lines[1].strip():
                        begin(1, line.strip(), block)
                    elif active is not None and index == 1 and units[active]["title"] == line.strip() and not units[active]["lines"]:
                        continue
                    else:
                        append_line(line, block)

        leaf_points: list[dict[str, Any]] = []
        for index, unit in enumerate(units):
            if unit["node_type"] not in {"chapter", "section"}:
                continue
            has_child = any(candidate.get("parent_index") == index for candidate in units)
            if has_child:
                continue
            leaf_points.append({
                "level": 3,
                "node_type": "knowledge_point",
                "title": unit["title"],
                "parent_index": index,
                "lines": list(unit["lines"]),
                "sources": dict(unit["sources"]),
            })
            unit["lines"] = []
        units.extend(leaf_points)

        with self.db.connect() as conn:
            node_ids: dict[int, str] = {}
            for order, unit in enumerate(units, 1):
                node_id = f"kn_{uuid.uuid4().hex}"
                node_ids[order - 1] = node_id
                parent_id = node_ids.get(unit["parent_index"])
                sources = list(unit["sources"].values())
                pages = sorted({int(source.get("page_number") or 1) for source in sources})
                conn.execute(
                    """INSERT INTO knowledge_nodes(node_id,course_id,document_id,node_scope,parent_id,node_type,title,
                       summary,markdown,keywords_json,source_pages_json,sort_order,analysis_job_id)
                       VALUES(?,?,?,'document',?,?,?,?,?,'[]',?,?,?)""",
                    (node_id, job["course_id"], job["document_id"], parent_id, unit["node_type"],
                     unit["title"], "", "\n".join(unit["lines"]), json.dumps(pages), order, analysis_job_id),
                )
                for source in sources:
                    conn.execute(
                        """INSERT OR IGNORE INTO knowledge_node_sources(node_id,block_id,document_id,page_number,bbox_json)
                           VALUES(?,?,?,?,?)""",
                        (node_id, source["block_id"], job["document_id"], source["page_number"], source["bbox_json"]),
                    )

    @staticmethod
    def _outline_fingerprint(material_type: str, kind: str, values: list[str]) -> str:
        payload = json.dumps(
            [material_type, kind, *sorted(str(value) for value in values)],
            ensure_ascii=False, separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _document_material_type(self, document_id: str) -> str:
        row = self.db.fetch_one(
            "SELECT material_type FROM document_material_metadata WHERE document_id=?",
            (document_id,),
        ) or {}
        value = str(row.get("material_type") or "other")
        return value if value in MATERIAL_TYPES else "other"

    @staticmethod
    def _syllabus_teaching_category(title: str, lineage: list[str]) -> str:
        """Classify syllabus administration/pedagogy separately from learnable knowledge."""
        text = " / ".join([*lineage, title])
        compact = re.sub(r"[\s\d一二三四五六七八九十、.．:：()（）\-_]+", "", text)
        title_compact = re.sub(r"[\s\d一二三四五六七八九十、.．:：()（）\-_]+", "", title)
        if any(marker in compact for marker in ("考核", "成绩评定", "评价方式", "评分标准")):
            return "assessment"
        if any(marker in compact for marker in ("培养目标", "课程目标", "学习目标", "教学目标")):
            return "objectives"
        if any(marker in compact for marker in (
            "教学方法", "教学模式", "学习模式", "学习模板", "教学设计", "课程思政计划",
        )):
            return "teaching_design"
        if any(marker in compact for marker in (
            "课程基本信息", "课程介绍", "课程性质", "课程定位", "先修知识", "学习资料",
        )) or title_compact in {"学分", "学时", "课程代码", "适用专业", "开课单位"}:
            return "course_profile"
        return ""

    def _refresh_teaching_archive_domains(self, course_id: str, document_id: str | None = None) -> None:
        params: tuple[Any, ...] = (course_id,)
        document_condition = ""
        if document_id:
            document_condition = " AND n.document_id=?"
            params += (document_id,)
        rows = self.db.fetch_all(
            f"""SELECT n.* FROM knowledge_nodes n
                 LEFT JOIN document_material_metadata m ON m.document_id=n.document_id
                 WHERE n.course_id=? {document_condition}
                   AND n.node_type IN ('chapter','section','knowledge_point')
                   AND (n.material_type='syllabus' OR COALESCE(m.material_type,'other')='syllabus')""",
            params,
        )
        if not rows:
            return
        node_map = {str(row["node_id"]): row for row in rows}
        point_updates: list[tuple[str, str, str]] = []
        for row in rows:
            if row["node_type"] != "knowledge_point":
                continue
            lineage: list[str] = []
            parent = node_map.get(str(row.get("parent_id") or ""))
            while parent:
                lineage.insert(0, str(parent["title"]))
                parent = node_map.get(str(parent.get("parent_id") or ""))
            category = self._syllabus_teaching_category(str(row["title"]), lineage)
            point_updates.append((
                "teaching_archive" if category else "knowledge", category, str(row["node_id"]),
            ))
        if not point_updates:
            return
        with self.db.connect() as conn:
            conn.executemany(
                """UPDATE knowledge_nodes SET content_domain=?,teaching_category=?,
                   updated_at=CURRENT_TIMESTAMP WHERE node_id=?""",
                point_updates,
            )
            if document_id:
                conn.execute(
                    """UPDATE document_blocks SET content_destination='excluded',
                           semantic_role='teaching_archive',include_as_knowledge=0,
                           verification_status='auto_verified',updated_at=CURRENT_TIMESTAMP
                       WHERE document_id=? AND block_id IN (
                           SELECT s.block_id FROM knowledge_node_sources s
                           JOIN knowledge_nodes n ON n.node_id=s.node_id
                           WHERE n.document_id=? AND n.content_domain='teaching_archive'
                       ) AND block_id NOT IN (
                           SELECT s.block_id FROM knowledge_node_sources s
                           JOIN knowledge_nodes n ON n.node_id=s.node_id
                           WHERE n.document_id=? AND n.content_domain='knowledge'
                             AND n.node_type='knowledge_point'
                       )""",
                    (document_id, document_id, document_id),
                )

    def _sync_knowledge_class_scopes(self, course_id: str) -> None:
        """Inherit class applicability from assigned source documents unless manually overridden."""
        with self.db.connect() as conn:
            conn.execute(
                """DELETE FROM knowledge_node_class_scopes
                   WHERE assignment_source='document' AND node_id IN (
                       SELECT node_id FROM knowledge_nodes WHERE course_id=?
                   )""",
                (course_id,),
            )
            conn.execute(
                """INSERT OR IGNORE INTO knowledge_node_class_scopes(
                       node_id,class_id,assignment_source,assigned_by
                   )
                   SELECT n.node_id,a.class_id,'document',a.assigned_by
                   FROM knowledge_nodes n
                   JOIN teaching_archive_document_assignments a ON a.document_id=n.document_id
                   WHERE n.course_id=? AND n.node_type='knowledge_point'
                     AND n.content_domain='knowledge'
                     AND n.teaching_scope_mode='inherited'""",
                (course_id,),
            )
            conn.execute(
                """INSERT OR IGNORE INTO knowledge_node_class_scopes(
                       node_id,class_id,assignment_source,assigned_by
                   )
                   SELECT DISTINCT n.node_id,a.class_id,'document',a.assigned_by
                   FROM knowledge_nodes n
                   JOIN knowledge_node_sources s ON s.node_id=n.node_id
                   JOIN teaching_archive_document_assignments a ON a.document_id=s.document_id
                   WHERE n.course_id=? AND n.node_scope='course'
                     AND n.node_type='knowledge_point' AND n.content_domain='knowledge'
                     AND n.teaching_scope_mode='inherited'""",
                (course_id,),
            )

    def _set_node_class_scopes(
        self, actor: dict[str, Any], node_ids: list[str], class_ids: list[str]
    ) -> None:
        unique_classes = list(dict.fromkeys(str(value) for value in class_ids if str(value)))
        if unique_classes:
            placeholders = ",".join("?" for _ in unique_classes)
            rows = self.db.fetch_all(
                f"""SELECT class_id FROM classes WHERE class_id IN ({placeholders})
                   AND course_id=(SELECT course_id FROM knowledge_nodes WHERE node_id=?)
                   AND teacher_id=?""",
                (*unique_classes, node_ids[0], actor["user_id"]),
            )
            if {str(row["class_id"]) for row in rows} != set(unique_classes):
                raise ValidationError("知识点只能分配给当前课程的教学班")
        leaves = self.db.fetch_all(
            f"""SELECT node_id FROM knowledge_nodes WHERE node_type='knowledge_point'
               AND node_id IN ({','.join('?' for _ in node_ids)})""",
            tuple(node_ids),
        )
        leaf_ids = [str(row["node_id"]) for row in leaves]
        if not leaf_ids:
            return
        with self.db.connect() as conn:
            placeholders = ",".join("?" for _ in leaf_ids)
            conn.execute(
                f"""UPDATE knowledge_nodes SET teaching_scope_mode='manual',
                    updated_at=CURRENT_TIMESTAMP WHERE node_id IN ({placeholders})""",
                tuple(leaf_ids),
            )
            conn.execute(
                f"DELETE FROM knowledge_node_class_scopes WHERE node_id IN ({placeholders})",
                tuple(leaf_ids),
            )
            if unique_classes:
                conn.executemany(
                    """INSERT INTO knowledge_node_class_scopes(
                           node_id,class_id,assignment_source,assigned_by
                       ) VALUES(?,?,'manual',?)""",
                    [(node_id, class_id, actor["user_id"])
                     for node_id in leaf_ids for class_id in unique_classes],
                )

    def teaching_archive(
        self, actor: dict[str, Any], course_id: str, class_id: str | None = None
    ) -> dict[str, Any]:
        course = self.campus.require_access(course_id, str(actor["user_id"]), "teacher")
        if course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权查看该课程教学档案")
        self._refresh_teaching_archive_domains(course_id)
        if class_id:
            owned_class = self.db.fetch_one(
                "SELECT class_id FROM classes WHERE class_id=? AND course_id=? AND teacher_id=?",
                (class_id, course_id, actor["user_id"]),
            )
            if not owned_class:
                raise PermissionDenied("所选教学班不属于当前课程")
        class_filter = ""
        section_params: tuple[Any, ...] = (course_id,)
        if class_id:
            class_filter = """ AND (
                NOT EXISTS (SELECT 1 FROM teaching_archive_document_assignments any_assignment
                            WHERE any_assignment.document_id=n.document_id)
                OR EXISTS (SELECT 1 FROM teaching_archive_document_assignments selected_assignment
                           WHERE selected_assignment.document_id=n.document_id
                             AND selected_assignment.class_id=?))"""
            section_params += (class_id,)
        rows = self.db.fetch_all(
            f"""SELECT n.*,s.title section_title,c.title chapter_title,d.original_name
               FROM knowledge_nodes n
               LEFT JOIN knowledge_nodes s ON s.node_id=n.parent_id
               LEFT JOIN knowledge_nodes c ON c.node_id=s.parent_id
               JOIN course_documents d ON d.document_id=n.document_id
               WHERE n.course_id=? AND n.node_scope='document'
                 AND n.node_type='knowledge_point' AND n.content_domain='teaching_archive'
                 AND n.status!='rejected'
                 AND (n.analysis_job_id IS NULL OR n.analysis_job_id=(
                     SELECT sj.analysis_job_id FROM semantic_analysis_jobs sj
                     WHERE sj.document_id=n.document_id
                       AND sj.status IN ('review_required','completed')
                     ORDER BY sj.created_at DESC LIMIT 1
                 )) {class_filter}
               ORDER BY d.created_at,n.sort_order""",
            section_params,
        )
        sections: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item.pop("summary", None)
            item["category_label"] = TEACHING_CATEGORY_LABELS.get(
                str(item.get("teaching_category") or ""), "其他教学信息"
            )
            item["source_pages"] = json.loads(item.pop("source_pages_json") or "[]")
            item["sources"] = self.db.fetch_all(
                """SELECT s.block_id,s.page_number,s.bbox_json,d.original_name
                   FROM knowledge_node_sources s JOIN course_documents d USING(document_id)
                   WHERE s.node_id=? ORDER BY s.page_number""",
                (item["node_id"],),
            )
            sections.append(item)
        classes = self.db.fetch_all(
            """SELECT cl.*,t.term_name,t.academic_year,t.teaching_period,
                      (SELECT COUNT(*) FROM class_memberships cm
                       WHERE cm.class_id=cl.class_id AND cm.status='active') member_count
               FROM classes cl JOIN terms t ON t.term_id=cl.term_id
               WHERE cl.course_id=? AND cl.teacher_id=?
               ORDER BY COALESCE(NULLIF(t.academic_year,''),t.term_name) DESC,
                        t.teaching_period,cl.class_variant,cl.teaching_time_slot,cl.class_name""",
            (course_id, actor["user_id"]),
        )
        documents = self.db.fetch_all(
            """SELECT d.document_id,d.original_name,d.created_at,j.status,j.progress,
                      s.status analysis_status,
                      GROUP_CONCAT(cl.class_id) class_ids,
                      GROUP_CONCAT(CASE WHEN cl.class_variant!='' THEN cl.class_variant ELSE cl.class_name END) class_labels
               FROM course_documents d
               JOIN teaching_archive_document_assignments a ON a.document_id=d.document_id
               JOIN classes cl ON cl.class_id=a.class_id
               LEFT JOIN ingestion_jobs j ON j.document_id=d.document_id
               LEFT JOIN semantic_analysis_jobs s ON s.analysis_job_id=(
                   SELECT latest.analysis_job_id FROM semantic_analysis_jobs latest
                   WHERE latest.document_id=d.document_id ORDER BY latest.created_at DESC LIMIT 1
               )
               WHERE d.course_id=?
               GROUP BY d.document_id,d.original_name,d.created_at,j.status,j.progress,s.status
               ORDER BY d.created_at DESC""",
            (course_id,),
        )
        for document in documents:
            document["class_ids"] = [value for value in str(document.get("class_ids") or "").split(",") if value]
            document["class_labels"] = [value for value in str(document.get("class_labels") or "").split(",") if value]
        return {
            "course": course,
            "sections": sections,
            "classes": classes,
            "documents": documents,
            "selected_class_id": class_id,
            "category_labels": TEACHING_CATEGORY_LABELS,
        }

    def _ensure_partitioned_course_outline(self, course_id: str) -> None:
        """Locally backfill material partitions without touching published versions."""
        # Early semantic-analysis builds could leave a PPT with chapters and sections
        # only.  Reconstruct those unreviewed outlines from persisted blocks before
        # creating course partitions.  The new outline is committed first; the old
        # nodes are removed only after at least one traceable leaf was produced.
        leafless_documents = self.db.fetch_all(
            """SELECT d.document_id
               FROM course_documents d
               WHERE d.course_id=?
                 AND EXISTS (
                     SELECT 1 FROM knowledge_nodes n
                     WHERE n.document_id=d.document_id AND n.node_scope='document'
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM knowledge_nodes n
                     WHERE n.document_id=d.document_id AND n.node_scope='document'
                       AND n.node_type='knowledge_point'
                 )
                 AND EXISTS (
                     SELECT 1 FROM document_blocks b
                     WHERE b.document_id=d.document_id
                       AND b.content_destination IN ('knowledge','unclassified')
                       AND LENGTH(TRIM(COALESCE(NULLIF(b.markdown,''),b.plain_text,'')))>0
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM knowledge_nodes n
                     WHERE n.document_id=d.document_id AND n.node_scope='document'
                       AND (n.reviewed_by IS NOT NULL OR n.status!='draft')
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM knowledge_nodes n
                     JOIN knowledge_version_nodes vn ON vn.node_id=n.node_id
                     WHERE n.document_id=d.document_id AND n.node_scope='document'
                 )""",
            (course_id,),
        )
        for document in leafless_documents:
            document_id = str(document["document_id"])
            job = self.db.fetch_one(
                """SELECT * FROM semantic_analysis_jobs
                   WHERE document_id=? AND status IN ('review_required','completed')
                   ORDER BY created_at DESC LIMIT 1""",
                (document_id,),
            )
            if not job:
                continue
            old_ids = [
                str(row["node_id"])
                for row in self.db.fetch_all(
                    """SELECT node_id FROM knowledge_nodes
                       WHERE document_id=? AND node_scope='document'""",
                    (document_id,),
                )
            ]
            try:
                self._build_faithful_document_outline(
                    job, str(job["analysis_job_id"]), include_unclassified=True
                )
            except Exception:
                # The legacy outline remains the visible source of truth.
                continue
            new_leaf = self.db.fetch_one(
                f"""SELECT COUNT(*) n FROM knowledge_nodes
                    WHERE document_id=? AND node_scope='document'
                      AND node_type='knowledge_point'
                      AND node_id NOT IN ({','.join('?' for _ in old_ids)})""",
                (document_id, *old_ids),
            ) if old_ids else None
            if not new_leaf or int(new_leaf["n"]) == 0:
                with self.db.connect() as conn:
                    conn.execute(
                        f"""DELETE FROM knowledge_nodes WHERE document_id=? AND node_scope='document'
                            AND node_id NOT IN ({','.join('?' for _ in old_ids)})""",
                        (document_id, *old_ids),
                    )
                continue
            with self.db.connect() as conn:
                conn.execute(
                    f"DELETE FROM knowledge_nodes WHERE node_id IN ({','.join('?' for _ in old_ids)})",
                    old_ids,
                )

        source_types = {
            str(row["material_type"] or "other")
            for row in self.db.fetch_all(
                """SELECT DISTINCT COALESCE(m.material_type,'other') material_type
                   FROM knowledge_nodes n JOIN course_documents d ON d.document_id=n.document_id
                   LEFT JOIN document_material_metadata m ON m.document_id=d.document_id
                   WHERE n.course_id=? AND n.node_scope='document'
                     AND n.node_type='knowledge_point' AND n.status!='rejected'
                     AND n.content_domain='knowledge'""",
                (course_id,),
            )
        }
        current_types = {
            str(row["material_type"])
            for row in self.db.fetch_all(
                """SELECT material_type FROM course_outline_generations
                   WHERE course_id=? AND status='current'""", (course_id,)
            )
        }
        for material_type in sorted(source_types - current_types):
            latest = self.db.fetch_one(
                """SELECT s.analysis_job_id FROM semantic_analysis_jobs s
                   JOIN course_documents d ON d.document_id=s.document_id
                   LEFT JOIN document_material_metadata m ON m.document_id=d.document_id
                   WHERE s.course_id=? AND COALESCE(m.material_type,'other')=?
                     AND s.status IN ('review_required','completed')
                   ORDER BY s.created_at DESC LIMIT 1""",
                (course_id, material_type),
            ) or {}
            self._rebuild_course_outline(
                course_id, latest.get("analysis_job_id"), use_api=False,
                material_type=material_type,
            )

    def _rebuild_course_outline(self, course_id: str, analysis_job_id: str | None, *,
                                use_api: bool = True,
                                material_type: str | None = None) -> str:
        if material_type is None and analysis_job_id:
            analysis = self.db.fetch_one(
                "SELECT document_id FROM semantic_analysis_jobs WHERE analysis_job_id=?",
                (analysis_job_id,),
            ) or {}
            material_type = self._document_material_type(str(analysis.get("document_id") or ""))
        material_type = material_type if material_type in MATERIAL_TYPES else "other"
        self._refresh_teaching_archive_domains(course_id)
        if analysis_job_id:
            analysis = self.db.fetch_one(
                "SELECT document_id FROM semantic_analysis_jobs WHERE analysis_job_id=?",
                (analysis_job_id,),
            ) or {}
            if analysis.get("document_id"):
                self.db.execute(
                    "UPDATE knowledge_nodes SET material_type=? WHERE document_id=? AND node_scope='document'",
                    (material_type, analysis["document_id"]),
                )
        source_nodes = self.db.fetch_all(
            """SELECT n.*,s.title section_title,c.title chapter_title,
                      COALESCE(m.material_type,'other') material_type,
                      COALESCE(m.classification_status,'suggested') classification_status
               FROM knowledge_nodes n
               LEFT JOIN knowledge_nodes s ON s.node_id=n.parent_id
               LEFT JOIN knowledge_nodes c ON c.node_id=s.parent_id
               JOIN course_documents d ON d.document_id=n.document_id
               LEFT JOIN document_material_metadata m ON m.document_id=d.document_id
               WHERE n.course_id=? AND n.node_scope='document' AND n.node_type='knowledge_point'
                 AND n.status!='rejected' AND n.content_domain='knowledge'
                 AND COALESCE(m.material_type,'other')=?
                 AND (n.analysis_job_id IS NULL OR n.analysis_job_id=(
                     SELECT sj.analysis_job_id FROM semantic_analysis_jobs sj
                     WHERE sj.document_id=n.document_id
                       AND sj.status IN ('running','review_required','completed')
                     ORDER BY sj.created_at DESC LIMIT 1
                 ))
               ORDER BY d.created_at,n.sort_order""", (course_id, material_type),
        )
        if not source_nodes:
            with self.db.connect() as conn:
                conn.execute(
                    """UPDATE course_outline_generations SET status='superseded',completed_at=CURRENT_TIMESTAMP
                       WHERE course_id=? AND material_type=? AND status='current'""",
                    (course_id, material_type),
                )
            return ""
        fallback_reason = ""
        if not use_api:
            unified = {
                "points": [{
                    "course_key": f"local-course-{index + 1}",
                    "chapter": row.get("chapter_title") or "待教师整理",
                    "section": row.get("section_title") or "本地结构识别",
                    "title": row["title"],
                    "keywords": json.loads(row.get("keywords_json") or "[]"),
                    "source_node_ids": [row["node_id"]],
                } for index, row in enumerate(source_nodes)],
                "relations": [],
                "analysis_mode": "local",
            }
        else:
            try:
                unified = self.semantic.unify_course_outline(
                    source_nodes,
                    on_call=(lambda: self._analysis_call(analysis_job_id)) if analysis_job_id else None,
                )
            except Exception as exc:
                message = str(exc).lower()
                transient = any(marker in message for marker in (
                    "timeout", "timed out", "readtimeout", "connectionerror",
                    "无法连接智能服务", "智能服务暂时超时", "自动续跑",
                    "qwen_connection_failed",
                ))
                structured = self._is_structured_output_error(exc)
                if not structured and not transient:
                    raise
                if material_type == "syllabus":
                    detail = "暂时不可用" if transient else "返回的 JSON 不完整"
                    raise ValidationError(
                        f"教学大纲课程归并 API {detail}；"
                        "本次分析已停止且未采用安全降级，请重试"
                    ) from exc
                fallback_reason = (
                    "课程归并 API 暂不可用，已按教材原始章节顺序生成可审核课程树"
                    if transient else
                    "课程归并 JSON 被截断，保留文档知识点供教师审核"
                )
                unified = {
                    "points": [{
                        "course_key": f"safe-course-{index + 1}",
                        "chapter": row.get("chapter_title") or "待教师整理",
                        "section": row.get("section_title") or "教材原始顺序",
                        "title": row["title"],
                        "keywords": json.loads(row.get("keywords_json") or "[]"),
                        "source_node_ids": [row["node_id"]],
                    } for index, row in enumerate(source_nodes)],
                    "relations": [],
                    "fallback_reason": fallback_reason,
                }
        suggestions = unified.get("points") if isinstance(unified, dict) else None
        relations = unified.get("relations") if isinstance(unified, dict) else None
        if not isinstance(suggestions, list) or not suggestions:
            raise ValidationError("AI 未生成课程统一目录")
        if not isinstance(relations, list):
            relations = []
        source_map = {row["node_id"]: row for row in source_nodes}
        source_position = {row["node_id"]: index for index, row in enumerate(source_nodes)}

        def suggestion_order(item: dict[str, Any]) -> tuple[Any, ...]:
            position = min(
                (source_position.get(str(value), len(source_position))
                 for value in item.get("source_node_ids", [])),
                default=len(source_position),
            )
            chapter = str(item.get("chapter") or "")
            section = str(item.get("section") or "")
            title = str(item.get("title") or "")
            chapter_parts = self._outline_number_parts(chapter)
            section_parts = self._outline_number_parts(section)
            title_parts = self._outline_number_parts(title)
            normalized_chapter = self._normalized_outline_title(chapter)
            if any(marker in normalized_chapter for marker in ("导学", "前言", "引言", "课程简介")):
                group = 0
                effective_parts: tuple[int, ...] = ()
            elif chapter_parts or section_parts or title_parts:
                group = 1
                effective_parts = chapter_parts or section_parts or title_parts
            else:
                group = 2
                effective_parts = ()
            return (
                group, effective_parts,
                position,
                0 if section_parts else 1, section_parts,
                0 if title_parts else 1, title_parts,
            )

        suggestions = sorted(
            (item for item in suggestions if isinstance(item, dict)),
            key=suggestion_order,
        )
        previous_generation = self.db.fetch_one(
            """SELECT generation_id FROM course_outline_generations
               WHERE course_id=? AND material_type=? AND status='current'""",
            (course_id, material_type),
        ) or {}
        previous_nodes = self.db.fetch_all(
            """SELECT * FROM knowledge_nodes WHERE course_id=? AND node_scope='course'
               AND material_type=? AND generation_id=? AND status!='rejected'""",
            (course_id, material_type, previous_generation.get("generation_id")),
        ) if previous_generation else []
        previous_by_fingerprint = {
            str(row.get("source_fingerprint") or ""): row
            for row in previous_nodes if str(row.get("source_fingerprint") or "")
        }
        previous_relations: dict[tuple[str, str, str], dict[str, Any]] = {}
        if previous_generation:
            for row in self.db.fetch_all(
                """SELECT r.*,s.source_fingerprint source_fingerprint,
                          t.source_fingerprint target_fingerprint
                   FROM knowledge_relations r
                   JOIN knowledge_nodes s ON s.node_id=r.source_node_id
                   JOIN knowledge_nodes t ON t.node_id=r.target_node_id
                   WHERE r.generation_id=? AND r.status!='rejected'""",
                (previous_generation["generation_id"],),
            ):
                previous_relations[(
                    str(row["source_fingerprint"]), str(row["target_fingerprint"]),
                    str(row["relation_type"]),
                )] = row

        def preserved_values(prior: dict[str, Any] | None, *, title: str,
                             markdown: str = "", keywords_json: str = "[]") -> tuple[Any, ...]:
            preserve = bool(prior and (prior.get("reviewed_by") or prior.get("status") == "approved"))
            if preserve:
                return (
                    prior["title"], prior["markdown"], prior["keywords_json"],
                    prior["status"], prior.get("reviewed_by"), prior.get("reviewed_at"),
                )
            return title, markdown, keywords_json, "draft", None, None

        generation_id = f"kog_{uuid.uuid4().hex}"
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO course_outline_generations(
                       generation_id,course_id,material_type,analysis_job_id,status,fallback_reason
                   ) VALUES(?,?,?,?,'building',?)""",
                (generation_id, course_id, material_type, analysis_job_id, fallback_reason),
            )
            chapter_ids: dict[str, str] = {}
            section_ids: dict[tuple[str, str], str] = {}
            point_ids: dict[str, str] = {}
            point_fingerprints: dict[str, str] = {}
            order = 0
            for index, suggestion in enumerate(suggestions):
                source_ids = sorted(
                    dict.fromkeys(
                        str(value) for value in suggestion.get("source_node_ids", [])
                        if str(value) in source_map
                    ),
                    key=lambda value: source_position[value],
                )
                if not source_ids:
                    continue
                chapter = self._clean_title(suggestion.get("chapter"), "未分章")
                section = self._clean_title(suggestion.get("section"), "未分节")
                if chapter not in chapter_ids:
                    order += 1
                    chapter_ids[chapter] = f"kn_{uuid.uuid4().hex}"
                    chapter_fingerprint = self._outline_fingerprint(
                        material_type, "chapter", [chapter]
                    )
                    chapter_values = preserved_values(
                        previous_by_fingerprint.get(chapter_fingerprint), title=chapter
                    )
                    conn.execute(
                        """INSERT INTO knowledge_nodes(
                               node_id,course_id,node_scope,node_type,title,markdown,keywords_json,
                               sort_order,status,analysis_job_id,reviewed_by,reviewed_at,
                               material_type,generation_id,source_fingerprint
                           ) VALUES(?,?,'course','chapter',?,?,?,?,?,?,?,?,?,?,?)""",
                        (chapter_ids[chapter], course_id, chapter, chapter_values[1], chapter_values[2],
                         order, chapter_values[3], analysis_job_id, chapter_values[4], chapter_values[5],
                         material_type, generation_id, chapter_fingerprint),
                    )
                section_key = (chapter, section)
                if section_key not in section_ids:
                    order += 1
                    section_ids[section_key] = f"kn_{uuid.uuid4().hex}"
                    section_fingerprint = self._outline_fingerprint(
                        material_type, "section", [chapter, section]
                    )
                    section_values = preserved_values(
                        previous_by_fingerprint.get(section_fingerprint), title=section
                    )
                    conn.execute(
                        """INSERT INTO knowledge_nodes(
                               node_id,course_id,node_scope,parent_id,node_type,title,markdown,keywords_json,
                               sort_order,status,analysis_job_id,reviewed_by,reviewed_at,
                               material_type,generation_id,source_fingerprint
                           ) VALUES(?,?,'course',?,'section',?,?,?,?,?,?,?,?,?,?,?)""",
                        (section_ids[section_key], course_id, chapter_ids[chapter], section_values[0],
                         section_values[1], section_values[2], order, section_values[3], analysis_job_id,
                         section_values[4], section_values[5], material_type, generation_id,
                         section_fingerprint),
                    )
                node_id = f"kn_{uuid.uuid4().hex}"
                course_key = str(suggestion.get("course_key") or f"course-point-{index + 1}")
                point_ids[course_key] = node_id
                source_rows = [source_map[source_id] for source_id in source_ids]
                source_signatures = [
                    self._outline_fingerprint(
                        material_type, "source", [
                            str(row.get("document_id") or ""), str(row.get("title") or ""),
                            hashlib.sha256(str(row.get("markdown") or "").encode("utf-8")).hexdigest(),
                        ],
                    )
                    for row in source_rows
                ]
                point_fingerprint = self._outline_fingerprint(
                    material_type, "knowledge_point", source_signatures
                )
                point_fingerprints[course_key] = point_fingerprint
                markdown_parts: list[str] = []
                for row in source_rows:
                    lineage = []
                    if str(row.get("chapter_title") or "").strip():
                        lineage.append(f"# {row['chapter_title']}")
                    if str(row.get("section_title") or "").strip():
                        lineage.append(f"## {row['section_title']}")
                    if row["markdown"].strip():
                        lineage.append(row["markdown"])
                    if lineage:
                        markdown_parts.append("\n".join(lineage))
                markdown = "\n\n".join(markdown_parts)
                pages = sorted({page for row in source_rows for page in json.loads(row["source_pages_json"] or "[]")})
                keywords_json = json.dumps(suggestion.get("keywords") or [], ensure_ascii=False)
                point_values = preserved_values(
                    previous_by_fingerprint.get(point_fingerprint),
                    title=self._clean_title(suggestion.get("title"), "知识点"),
                    markdown=markdown, keywords_json=keywords_json,
                )
                order += 1
                conn.execute(
                    """INSERT INTO knowledge_nodes(node_id,course_id,node_scope,parent_id,node_type,title,summary,markdown,
                       keywords_json,source_pages_json,sort_order,status,analysis_job_id,reviewed_by,reviewed_at,
                       material_type,generation_id,source_fingerprint)
                       VALUES(?,?,'course',?,'knowledge_point',?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (node_id, course_id, section_ids[section_key],
                     point_values[0], "", point_values[1], point_values[2],
                     json.dumps(pages), order, point_values[3], analysis_job_id,
                     point_values[4], point_values[5], material_type, generation_id,
                     point_fingerprint),
                )
                for source_id in source_ids:
                    for source in conn.execute(
                        "SELECT * FROM knowledge_node_sources WHERE node_id=?", (source_id,)
                    ).fetchall():
                        conn.execute(
                            """INSERT OR IGNORE INTO knowledge_node_sources(node_id,block_id,document_id,page_number,bbox_json)
                               VALUES(?,?,?,?,?)""",
                            (node_id, source["block_id"], source["document_id"], source["page_number"], source["bbox_json"]),
                        )
            valid_types = {"parallel", "prerequisite", "follow_up", "related", "confusable"}
            symmetric = {"parallel", "related", "confusable"}
            for relation in relations:
                if not isinstance(relation, dict):
                    continue
                source = point_ids.get(str(relation.get("source_course_key") or ""))
                target = point_ids.get(str(relation.get("target_course_key") or ""))
                relation_type = str(relation.get("type") or "")
                if not source or not target or source == target or relation_type not in valid_types:
                    continue
                if relation_type in symmetric and source > target:
                    source, target = target, source
                try:
                    confidence = max(0.0, min(1.0, float(relation.get("confidence", 0))))
                except (TypeError, ValueError):
                    confidence = None
                source_key = str(relation.get("source_course_key") or "")
                target_key = str(relation.get("target_course_key") or "")
                source_fingerprint = point_fingerprints.get(source_key, "")
                target_fingerprint = point_fingerprints.get(target_key, "")
                if relation_type in symmetric and source_fingerprint > target_fingerprint:
                    source_fingerprint, target_fingerprint = target_fingerprint, source_fingerprint
                prior_relation = previous_relations.get(
                    (source_fingerprint, target_fingerprint, relation_type)
                )
                relation_status = (
                    str(prior_relation["status"])
                    if prior_relation and (prior_relation.get("reviewed_by") or prior_relation.get("status") == "approved")
                    else "draft"
                )
                conn.execute(
                    """INSERT OR IGNORE INTO knowledge_relations(relation_id,course_id,source_node_id,target_node_id,
                       relation_type,confidence,reason,status,reviewed_by,reviewed_at,material_type,generation_id)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (f"kr_{uuid.uuid4().hex}", course_id, source, target, relation_type, confidence,
                     str(relation.get("reason") or "")[:500], relation_status,
                     prior_relation.get("reviewed_by") if prior_relation else None,
                     prior_relation.get("reviewed_at") if prior_relation else None,
                     material_type, generation_id),
                )
            conn.execute(
                """UPDATE course_outline_generations SET status='superseded',completed_at=CURRENT_TIMESTAMP
                   WHERE course_id=? AND material_type=? AND status='current'""",
                (course_id, material_type),
            )
            conn.execute(
                """UPDATE course_outline_generations SET status='current',completed_at=CURRENT_TIMESTAMP
                   WHERE generation_id=?""", (generation_id,),
            )
        return fallback_reason

    @staticmethod
    def _analysis_response(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        row.pop("ai_key_encrypted", None)
        try:
            result = json.loads(row.get("result_json") or "{}")
        except json.JSONDecodeError:
            result = {}
        warnings: list[str] = []
        skipped = int(result.get("skipped_blocks") or 0)
        fallback_batches = result.get("fallback_batches") or []
        if skipped:
            warnings.append(f"已跳过 {skipped} 个图片或空白内容块")
        if fallback_batches:
            warnings.append(
                f"{len(fallback_batches)} 个批次的模型 JSON 被截断，已按原文安全降级"
            )
        for key in (
            "presentation_outline_fallback", "document_reduce_fallback",
            "course_reduce_fallback", "notice",
        ):
            if result.get(key):
                warnings.append(str(result[key]))
        checkpoint_repairs = result.get("checkpoint_repairs") or []
        if checkpoint_repairs:
            warnings.append(
                f"{len(checkpoint_repairs)} 个旧分析断点字段不完整，已从落库原文安全修复"
            )
        row["warnings"] = warnings
        row["analysis_summary"] = {
            "skipped_blocks": skipped,
            "fallback_batches": len(fallback_batches),
            "used_document_fallback": bool(result.get("document_reduce_fallback")),
            "used_course_fallback": bool(result.get("course_reduce_fallback")),
        }
        error = str(row.get("error_message") or "").lower()
        non_retryable = any(marker in error for marker in (
            "http 401", "http 403", "quota exhausted", "insufficient_quota",
            "invalid api key", "unauthorized", "forbidden", "权限或额度问题",
        ))
        row["retryable"] = row.get("status") == "failed" and not non_retryable
        row["input_source"] = "persisted_document_ir"
        total = int(row.get("total_batches") or 0)
        current = int(row.get("current_batch") or 0)
        row["progress"] = (
            100 if row.get("status") in {"review_required", "completed"}
            else max(0, min(99, round(current * 100 / total))) if total
            else 0
        )
        return row

    def get_analysis_job(self, actor: dict[str, Any], analysis_job_id: str) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM semantic_analysis_jobs WHERE analysis_job_id=?", (analysis_job_id,))
        if not row:
            raise NotFound("语义分析任务不存在")
        course = self.campus.require_access(row["course_id"], str(actor["user_id"]), "teacher")
        if course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权查看该分析任务")
        return self._analysis_response(row) or {}

    def latest_analysis_for_document(self, actor: dict[str, Any], document_id: str) -> dict[str, Any] | None:
        self.require_document_access(actor, document_id)
        return self._analysis_response(self.db.fetch_one(
            "SELECT * FROM semantic_analysis_jobs WHERE document_id=? ORDER BY created_at DESC LIMIT 1", (document_id,),
        ))

    def retry_analysis(self, actor: dict[str, Any], analysis_job_id: str) -> dict[str, Any]:
        job = self.get_analysis_job(actor, analysis_job_id)
        if job["status"] != "failed":
            raise ValidationError("只有失败的语义分析任务可以重试")
        try:
            checkpoint = json.loads(job.get("result_json") or "{}")
        except json.JSONDecodeError:
            checkpoint = {}
        reset_batch = checkpoint.get("schema_version") != 5 or not isinstance(
            checkpoint.get("map_results"), list
        )
        self.db.execute(
            """UPDATE semantic_analysis_jobs SET status='queued',current_stage='queued',error_message='',
               retry_count=0,next_retry_at=NULL,
               current_batch=CASE WHEN ? THEN 0 ELSE current_batch END,
               total_batches=CASE WHEN ? THEN 0 ELSE total_batches END,
               result_json=CASE WHEN ? THEN '{}' ELSE result_json END,
               updated_at=CURRENT_TIMESTAMP WHERE analysis_job_id=?""",
            (int(reset_batch), int(reset_batch), int(reset_batch), analysis_job_id),
        )
        return self.get_analysis_job(actor, analysis_job_id)

    def cancel_analysis(self, actor: dict[str, Any], analysis_job_id: str) -> dict[str, Any]:
        job = self.get_analysis_job(actor, analysis_job_id)
        if job["status"] not in {"queued", "running", "retry_wait"}:
            raise ValidationError("当前语义分析任务不能取消")
        self.db.execute(
            "UPDATE semantic_analysis_jobs SET status='cancelled',current_stage='cancelled' WHERE analysis_job_id=?",
            (analysis_job_id,),
        )
        return self.get_analysis_job(actor, analysis_job_id)

    def get_job(self, actor: dict[str, Any], job_id: str) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM ingestion_jobs WHERE job_id=?", (job_id,))
        if not row:
            raise NotFound("入库任务不存在")
        course = self.campus.require_access(row["course_id"], str(actor["user_id"]), str(actor["role"]))
        if actor["role"] != "teacher" or course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权查看该入库任务")
        row.pop("ai_key_encrypted", None)
        return row

    def list_jobs(self, actor: dict[str, Any], course_id: str) -> list[dict[str, Any]]:
        course = self.campus.require_access(course_id, str(actor["user_id"]), "teacher")
        if course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权查看该课程任务")
        rows = self.db.fetch_all(
            """SELECT j.*,d.original_name,d.mime_type,d.size_bytes,d.student_file_visible,
                      COALESCE(m.material_type,'other') material_type,
                      COALESCE(m.suggested_material_type,'other') suggested_material_type,
                      COALESCE(m.classification_status,'suggested') classification_status,
                      COALESCE(m.tags_json,'[]') tags_json,
                      COALESCE(m.classification_reason,'') classification_reason,
                      (SELECT s.analysis_job_id FROM semantic_analysis_jobs s WHERE s.document_id=j.document_id ORDER BY s.created_at DESC LIMIT 1) analysis_job_id,
                      (SELECT s.status FROM semantic_analysis_jobs s WHERE s.document_id=j.document_id ORDER BY s.created_at DESC LIMIT 1) analysis_status,
                      (SELECT s.current_stage FROM semantic_analysis_jobs s WHERE s.document_id=j.document_id ORDER BY s.created_at DESC LIMIT 1) analysis_stage,
                       (SELECT s.error_message FROM semantic_analysis_jobs s WHERE s.document_id=j.document_id ORDER BY s.created_at DESC LIMIT 1) analysis_error,
                       (SELECT s.current_batch FROM semantic_analysis_jobs s WHERE s.document_id=j.document_id ORDER BY s.created_at DESC LIMIT 1) analysis_current_batch,
                       (SELECT s.total_batches FROM semantic_analysis_jobs s WHERE s.document_id=j.document_id ORDER BY s.created_at DESC LIMIT 1) analysis_total_batches,
                       (SELECT s.api_calls FROM semantic_analysis_jobs s WHERE s.document_id=j.document_id ORDER BY s.created_at DESC LIMIT 1) analysis_api_calls,
                       (SELECT COUNT(*) FROM document_blocks b WHERE b.document_id=j.document_id) document_block_count
               FROM ingestion_jobs j JOIN course_documents d USING(document_id)
               LEFT JOIN document_material_metadata m USING(document_id)
               WHERE j.course_id=? ORDER BY j.created_at DESC""", (course_id,),
        )
        for row in rows:
            row["tags"] = json.loads(row.pop("tags_json") or "[]")
            row.pop("ai_key_encrypted", None)
        return rows

    def get_manifest(self, actor: dict[str, Any], document_id: str) -> dict[str, Any]:
        """Return the persisted page/batch checkpoint without exposing raw files."""
        document = self.require_document_access(actor, document_id)
        if actor.get("role") != "teacher" or document["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权查看该资料的解析清单")
        job = self.db.fetch_one(
            "SELECT manifest_path FROM ingestion_jobs WHERE document_id=? ORDER BY created_at DESC LIMIT 1",
            (document_id,),
        )
        manifest_path = Path(str((job or {}).get("manifest_path") or ""))
        if not manifest_path.is_file():
            return {
                "document_id": document_id, "status": "not_started", "total_pages": 0,
                "pages": {}, "batches": {}, "errors": [],
            }
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValidationError(f"解析清单不可读：{str(exc)[:300]}") from exc
        payload.pop("native_text", None)
        return payload

    def list_document_pages(self, actor: dict[str, Any], document_id: str) -> list[dict[str, Any]]:
        document = self.require_document_access(actor, document_id)
        if actor.get("role") != "teacher" or document["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权查看该资料页面")
        return self.db.fetch_all(
            "SELECT * FROM document_pages WHERE document_id=? ORDER BY page_index",
            (document_id,),
        )

    def get_document_page(self, actor: dict[str, Any], document_id: str, page_number: int) -> dict[str, Any]:
        if page_number < 1:
            raise ValidationError("页码必须从 1 开始")
        pages = self.list_document_pages(actor, document_id)
        page = next((row for row in pages if int(row["page_number"]) == page_number), None)
        if not page:
            raise NotFound("页面不存在")
        blocks = self.db.fetch_all(
            "SELECT * FROM document_blocks WHERE document_id=? AND page_number=? ORDER BY block_order",
            (document_id, page_number),
        )
        page["blocks"] = blocks
        return page

    def get_document_structure(self, actor: dict[str, Any], document_id: str) -> dict[str, Any]:
        document = self.require_document_access(actor, document_id)
        if actor.get("role") != "teacher" or document["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权查看该资料结构")
        row = self.db.fetch_one("SELECT * FROM document_structures WHERE document_id=?", (document_id,))
        if not row:
            return {"document_id": document_id, "status": "not_started", "outline": [], "toc_entries": [], "warnings": []}
        for field in ("outline_json", "toc_entries_json", "warnings_json"):
            value = row.pop(field)
            try:
                row[field[:-5]] = json.loads(value or "[]")
            except json.JSONDecodeError:
                row[field[:-5]] = []
        row["document_id"] = document_id
        return row

    def list_presentation_slides(self, actor: dict[str, Any], document_id: str) -> list[dict[str, Any]]:
        document = self.require_document_access(actor, document_id)
        if actor.get("role") != "teacher" or document["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权查看该演示文稿结构")
        rows = self.db.fetch_all(
            "SELECT * FROM presentation_slides WHERE document_id=? ORDER BY slide_index", (document_id,)
        )
        for row in rows:
            row["slide_number"] = int(row.get("slide_index") or 0) + 1
            for field in ("reading_order_json", "shapes_json", "regions_json"):
                try:
                    row[field[:-5]] = json.loads(row.pop(field) or "[]")
                except json.JSONDecodeError:
                    row[field[:-5]] = []
        return rows

    def _candidate_access(self, actor: dict[str, Any], candidate_id: str) -> dict[str, Any]:
        candidate = self.db.fetch_one(
            """SELECT k.*,c.owner_id,d.course_id AS document_course_id
               FROM knowledge_candidates k JOIN course_documents d USING(document_id)
               JOIN courses c ON c.course_id=d.course_id
               WHERE k.candidate_id=?""", (candidate_id,),
        )
        if not candidate:
            raise NotFound("知识候选不存在")
        document = self.require_document_access(actor, candidate["document_id"])
        if actor.get("role") != "teacher" or document["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权审核该知识候选")
        return candidate

    def _candidate_response(self, candidate: dict[str, Any]) -> dict[str, Any]:
        row = dict(candidate)
        for field in ("source_block_ids_json", "bbox_json", "chapter_path_json"):
            target = field[:-5]
            try:
                row[target] = json.loads(row.pop(field) or "[]")
            except json.JSONDecodeError:
                row[target] = []
        source_ids = self._candidate_source_ids(row)
        row["source_block_ids"] = source_ids
        row["source_blocks"] = self.db.fetch_all(
            """SELECT b.block_id,b.page_index,b.block_order,b.block_type,b.page_number,b.bbox_json,
                      b.markdown,b.plain_text,b.latex,b.source_image_path,b.confidence,
                      b.verification_status,b.content_destination,b.include_as_knowledge,
                      b.region_type,b.region_confidence,b.region_reason,b.chapter_path_json
               FROM knowledge_candidate_blocks cb
               JOIN document_blocks b ON b.block_id=cb.block_id
               WHERE cb.candidate_id=? ORDER BY cb.sort_order""",
            (row["candidate_id"],),
        ) if source_ids else []
        if not row["source_blocks"] and source_ids:
            # Keep older candidate rows readable if their link rows were created
            # before knowledge_candidate_blocks was introduced.
            row["source_blocks"] = self.db.fetch_all(
                """SELECT block_id,page_index,block_order,block_type,page_number,bbox_json,
                          markdown,plain_text,latex,source_image_path,confidence,
                          verification_status,content_destination,include_as_knowledge,
                          region_type,region_confidence,region_reason,chapter_path_json
                   FROM document_blocks WHERE block_id IN ({})
                   ORDER BY page_index,block_order""".format(
                    ",".join("?" for _ in source_ids) or "NULL"
                ),
                tuple(source_ids),
            )
        for block in row["source_blocks"]:
            for field in ("bbox_json", "chapter_path_json"):
                try:
                    block[field[:-5]] = json.loads(block.pop(field) or "[]")
                except json.JSONDecodeError:
                    block[field[:-5]] = []
        row["source_markdown"] = "\n\n".join(
            str(block.get("markdown") or block.get("latex") or block.get("plain_text") or "").strip()
            for block in row["source_blocks"]
            if str(block.get("markdown") or block.get("latex") or block.get("plain_text") or "").strip()
        )
        row["source_pages"] = sorted({
            int(block["page_number"]) for block in row["source_blocks"]
            if block.get("page_number") is not None
        })
        row["source_locations"] = [
            {"block_id": block["block_id"], "page_number": block.get("page_number"), "bbox": block.get("bbox", [])}
            for block in row["source_blocks"]
        ]
        return row

    def _candidate_source_ids(self, candidate: dict[str, Any]) -> list[str]:
        linked = self.db.fetch_all(
            """SELECT block_id FROM knowledge_candidate_blocks
               WHERE candidate_id=? ORDER BY sort_order""",
            (candidate["candidate_id"],),
        )
        if linked:
            return [str(row["block_id"]) for row in linked]
        if "source_block_ids" in candidate:
            value = candidate.get("source_block_ids") or []
        else:
            try:
                value = json.loads(candidate.get("source_block_ids_json") or "[]")
            except json.JSONDecodeError:
                value = []
        return list(dict.fromkeys(str(item) for item in value if str(item).strip()))

    def _candidate_document_node(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        """Return the current document-tree leaf with the exact same evidence set."""
        source_ids = set(self._candidate_source_ids(candidate))
        if not source_ids:
            return None
        latest = self.db.fetch_one(
            """SELECT analysis_job_id FROM semantic_analysis_jobs WHERE document_id=?
               AND status IN ('review_required','completed')
               ORDER BY created_at DESC,rowid DESC LIMIT 1""",
            (candidate["document_id"],),
        ) or {}
        nodes = self.db.fetch_all(
            """SELECT * FROM knowledge_nodes WHERE document_id=? AND node_scope='document'
               AND node_type='knowledge_point' AND status!='rejected'
               AND content_domain='knowledge'
               AND (analysis_job_id=? OR analysis_job_id IS NULL)
               ORDER BY sort_order,node_id""",
            (candidate["document_id"], latest.get("analysis_job_id")),
        )
        for node in nodes:
            linked = {
                str(row["block_id"]) for row in self.db.fetch_all(
                    "SELECT block_id FROM knowledge_node_sources WHERE node_id=?",
                    (node["node_id"],),
                )
            }
            if linked != source_ids:
                continue
            node_map = {
                row["node_id"]: row for row in self.db.fetch_all(
                    """SELECT node_id,parent_id,title,node_type FROM knowledge_nodes
                       WHERE document_id=? AND node_scope='document'
                         AND (analysis_job_id=? OR analysis_job_id IS NULL)""",
                    (candidate["document_id"], latest.get("analysis_job_id")),
                )
            }
            path: list[str] = []
            current = node_map.get(node.get("parent_id"))
            while current:
                path.insert(0, str(current["title"]))
                current = node_map.get(current.get("parent_id"))
            return {**node, "chapter_path": path}
        return None

    @staticmethod
    def _has_substantive_node_markdown(title: str, markdown: str) -> bool:
        """Exclude structural heading placeholders from the reviewable leaf queue."""
        value = str(markdown or "").strip()
        if not value:
            return False
        without_heading_marks = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", value)
        without_formatting = re.sub(r"[`*_~>]", "", without_heading_marks)
        normalize = lambda text: re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text).lower()
        return bool(normalize(without_formatting)) and normalize(without_formatting) != normalize(title)

    def _sync_candidates_for_nodes(
        self, node_ids: list[str], status: str, actor_id: str
    ) -> list[str]:
        """Mirror document leaf review status to candidates with identical evidence."""
        review_status = {
            "approved": "APPROVED", "rejected": "REJECTED", "draft": "PENDING",
        }[status]
        candidate_ids: list[str] = []
        for node_id in dict.fromkeys(node_ids):
            node = self.db.fetch_one(
                """SELECT * FROM knowledge_nodes WHERE node_id=? AND node_scope='document'
                   AND node_type='knowledge_point'""",
                (node_id,),
            )
            if not node:
                continue
            node_sources = {
                str(row["block_id"]) for row in self.db.fetch_all(
                    "SELECT block_id FROM knowledge_node_sources WHERE node_id=?", (node_id,)
                )
            }
            if not node_sources:
                continue
            for candidate in self.db.fetch_all(
                "SELECT * FROM knowledge_candidates WHERE document_id=?",
                (node["document_id"],),
            ):
                if set(self._candidate_source_ids(candidate)) == node_sources:
                    candidate_ids.append(str(candidate["candidate_id"]))
        if candidate_ids:
            placeholders = ",".join("?" for _ in candidate_ids)
            self.db.execute(
                f"""UPDATE knowledge_candidates SET review_status=?,reviewed_by=?,
                    reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                    WHERE candidate_id IN ({placeholders})""",
                (review_status, actor_id, *candidate_ids),
            )
        return candidate_ids

    def list_knowledge_candidates(self, actor: dict[str, Any], document_id: str) -> list[dict[str, Any]]:
        document = self.require_document_access(actor, document_id)
        self._refresh_teaching_archive_domains(str(document["course_id"]), document_id)
        self._sync_knowledge_class_scopes(str(document["course_id"]))
        rows = self.db.fetch_all(
            "SELECT * FROM knowledge_candidates WHERE document_id=? ORDER BY page_start,candidate_id",
            (document_id,),
        )
        responses = [self._candidate_response(row) for row in rows]
        aligned: list[dict[str, Any]] = []
        for response, raw in zip(responses, rows):
            node = self._candidate_document_node(raw)
            if not node:
                continue
            # A semantic leaf must represent a reviewable section, not a copied
            # chapter/section heading with no body. Structural headings remain
            # visible as folders in knowledge governance.
            if not self._has_substantive_node_markdown(node["title"], node["markdown"]):
                continue
            response["document_node_id"] = node["node_id"]
            response["document_node_status"] = node["status"]
            response["title"] = node["title"]
            response["chapter_path"] = node["chapter_path"]
            response["tree_markdown"] = node["markdown"]
            response["section_markdown"] = node["markdown"]
            response["section_excerpt"] = re.sub(
                r"\s+", " ", re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", node["markdown"])
            ).strip()[:180]
            response["teaching_scopes"] = self.db.fetch_all(
                """SELECT ks.class_id,cl.class_name,cl.class_variant,t.academic_year,
                          t.teaching_period,t.term_name
                   FROM knowledge_node_class_scopes ks JOIN classes cl USING(class_id)
                   JOIN terms t USING(term_id) WHERE ks.node_id=?""",
                (node["node_id"],),
            )
            response["class_ids"] = [item["class_id"] for item in response["teaching_scopes"]]
            response["is_course_wide"] = not response["class_ids"]
            response["review_status"] = (
                "APPROVED" if node["status"] == "approved" else "PENDING"
            )
            aligned.append(response)
        # Before semantic-tree generation there is no leaf to align with yet;
        # retain the parser candidates so the source remains inspectable.
        has_document_leaves = bool(self.db.fetch_one(
            """SELECT 1 ok FROM knowledge_nodes WHERE document_id=?
               AND node_scope='document' AND node_type='knowledge_point'
               AND content_domain='knowledge' LIMIT 1""",
            (document_id,),
        ))
        return aligned if has_document_leaves else responses

    def update_knowledge_candidate(self, actor: dict[str, Any], candidate_id: str,
                                   updates: dict[str, Any]) -> dict[str, Any]:
        candidate = self._candidate_access(actor, candidate_id)
        if candidate["review_status"] == "REJECTED":
            raise ValidationError("已驳回的候选不能直接修改")
        title = str(updates.get("title", candidate["title"])).strip()[:160]
        knowledge_type = str(updates.get("knowledge_type", candidate["knowledge_type"])).strip()[:64]
        teacher_revision = str(updates.get("teacher_revision", candidate.get("teacher_revision") or ""))[:50000]
        if not title:
            raise ValidationError("知识点标题不能为空")
        status = "MODIFIED" if teacher_revision else candidate["review_status"]
        self.db.execute(
            """UPDATE knowledge_candidates SET title=?,knowledge_type=?,teacher_revision=?,review_status=?,
               reviewed_by=?,updated_at=CURRENT_TIMESTAMP WHERE candidate_id=?""",
            (title, knowledge_type, teacher_revision, status, actor["user_id"], candidate_id),
        )
        node = self._candidate_document_node(candidate)
        if node:
            self.db.execute(
                """UPDATE knowledge_nodes SET title=?,markdown=?,reviewed_by=?,
                   reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE node_id=?""",
                (title, teacher_revision or node["markdown"], actor["user_id"], node["node_id"]),
            )
        return self._candidate_response(self.db.fetch_one(
            "SELECT * FROM knowledge_candidates WHERE candidate_id=?", (candidate_id,)
        ) or candidate)

    def approve_knowledge_candidate(self, actor: dict[str, Any], candidate_id: str) -> dict[str, Any]:
        candidate = self._candidate_access(actor, candidate_id)
        if candidate["review_status"] == "REJECTED":
            raise ValidationError("已驳回的候选不能批准")
        source_ids = self._candidate_source_ids(candidate)
        if not source_ids:
            raise ValidationError("候选没有可追溯的原始 block")
        source_rows = self.db.fetch_all(
            """SELECT block_id,document_id,include_as_knowledge,region_type
               FROM document_blocks WHERE document_id=? AND block_id IN ({})""".format(
                ",".join("?" for _ in source_ids)
            ),
            (candidate["document_id"], *source_ids),
        )
        if {str(row["block_id"]) for row in source_rows} != set(source_ids):
            raise ValidationError("候选包含无法追溯到当前资料的原始 block")
        if any(not row["include_as_knowledge"] or row["region_type"] != "knowledge" for row in source_rows):
            raise ValidationError("候选包含已被排除的非知识区域，不能批准")
        node = self._candidate_document_node(candidate)
        has_document_leaves = bool(self.db.fetch_one(
            """SELECT 1 ok FROM knowledge_nodes WHERE document_id=?
               AND node_scope='document' AND node_type='knowledge_point' LIMIT 1""",
            (candidate["document_id"],),
        ))
        if not node and has_document_leaves:
            raise ValidationError("候选与文档独立目录的最小知识点不一致，请重新分析后再审核")
        with self.db.connect() as conn:
            placeholders = ",".join("?" for _ in source_ids)
            conn.execute(
                f"""UPDATE document_blocks SET content_destination='knowledge',semantic_role='source_markdown',
                    verification_status='teacher_verified',reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,
                    include_as_knowledge=1,updated_at=CURRENT_TIMESTAMP WHERE block_id IN ({placeholders})""",
                (actor["user_id"], *source_ids),
            )
            conn.execute(
                """UPDATE knowledge_candidates SET review_status='APPROVED',reviewed_by=?,
                   reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE candidate_id=?""",
                (actor["user_id"], candidate_id),
            )
            if node:
                conn.execute(
                    """UPDATE knowledge_nodes SET status='approved',reviewed_by=?,
                       reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE node_id=?""",
                    (actor["user_id"], node["node_id"]),
                )
                conn.execute("DELETE FROM knowledge_node_trash WHERE node_id=?", (node["node_id"],))
        self._sync_approved_source_blocks(candidate["document_id"])
        return self._candidate_response(self.db.fetch_one(
            "SELECT * FROM knowledge_candidates WHERE candidate_id=?", (candidate_id,)
        ) or candidate)

    def reject_knowledge_candidate(self, actor: dict[str, Any], candidate_id: str) -> dict[str, Any]:
        candidate = self._candidate_access(actor, candidate_id)
        node = self._candidate_document_node(candidate)
        source_ids = self._candidate_source_ids(candidate)
        with self.db.connect() as conn:
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                conn.execute(
                    f"""UPDATE document_blocks SET content_destination='excluded',semantic_role='teacher_rejected',
                        verification_status='rejected',reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,
                        updated_at=CURRENT_TIMESTAMP WHERE block_id IN ({placeholders})""",
                    (actor["user_id"], *source_ids),
                )
            conn.execute(
                """UPDATE knowledge_candidates SET review_status='REJECTED',reviewed_by=?,
                   reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE candidate_id=?""",
                (actor["user_id"], candidate_id),
            )
            if node:
                conn.execute(
                    """UPDATE knowledge_nodes SET status='rejected',reviewed_by=?,
                       reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE node_id=?""",
                    (actor["user_id"], node["node_id"]),
                )
                self._put_nodes_in_trash(
                    conn, [node], str(actor["user_id"]),
                    reason="知识点候选审核未通过", action_type="candidate_rejected",
                )
        self._sync_approved_source_blocks(candidate["document_id"])
        return self._candidate_response(self.db.fetch_one(
            "SELECT * FROM knowledge_candidates WHERE candidate_id=?", (candidate_id,)
        ) or candidate)

    def require_document_access(self, actor: dict[str, Any], document_id: str) -> dict[str, Any]:
        document = self.db.fetch_one(
            "SELECT d.*,c.course_type,c.owner_id FROM course_documents d JOIN courses c USING(course_id) WHERE document_id=?",
            (document_id,),
        )
        if not document:
            raise NotFound("资料不存在")
        user_id, role = str(actor["user_id"]), str(actor["role"])
        self.campus.require_access(document["course_id"], user_id, role)
        if role == "teacher":
            if document["owner_id"] != user_id:
                raise PermissionDenied("无权查看该课程原始资料")
        elif role == "student":
            if document["course_type"] != "shared_course" or not document["student_file_visible"]:
                raise PermissionDenied("教师尚未向学生开放该原始资料")
            published = self.db.fetch_one(
                """SELECT 1 ok FROM knowledge_versions v
                   JOIN knowledge_version_blocks vb USING(version_id)
                   JOIN document_blocks b USING(block_id)
                   WHERE v.course_id=? AND v.status='published' AND b.document_id=? LIMIT 1""",
                (document["course_id"], document_id),
            )
            if not published:
                raise PermissionDenied("该资料尚未随知识库发布")
        else:
            raise PermissionDenied("用户角色不合法")
        return document

    def set_student_file_visibility(self, actor: dict[str, Any], document_id: str, visible: bool) -> dict[str, Any]:
        document = self.require_document_access(actor, document_id)
        if actor["role"] != "teacher":
            raise PermissionDenied("仅教师可以设置原文件可见性")
        self.db.execute(
            "UPDATE course_documents SET student_file_visible=? WHERE document_id=?",
            (1 if visible else 0, document_id),
        )
        return self.require_document_access(actor, document_id)

    def update_material_metadata(self, actor: dict[str, Any], document_id: str, *,
                                 material_type: str, tags: list[str]) -> dict[str, Any]:
        document = self.require_document_access(actor, document_id)
        if actor.get("role") != "teacher" or document["owner_id"] != actor["user_id"]:
            raise PermissionDenied("仅课程教师可以整理资料用途")
        if material_type not in MATERIAL_TYPES:
            raise ValidationError("资料用途类型无效")
        old_material_type = self._document_material_type(document_id)
        clean_tags = list(dict.fromkeys(
            str(tag).strip()[:40] for tag in tags if str(tag).strip()
        ))[:20]
        self.db.execute(
            """INSERT INTO document_material_metadata(
                   document_id,material_type,suggested_material_type,classification_status,
                   tags_json,classification_reason,classified_by,classified_at
               ) VALUES(?,?,?,'confirmed',?,'教师确认',?,CURRENT_TIMESTAMP)
               ON CONFLICT(document_id) DO UPDATE SET
                   material_type=excluded.material_type,
                   classification_status='confirmed',tags_json=excluded.tags_json,
                   classified_by=excluded.classified_by,classified_at=CURRENT_TIMESTAMP,
                   updated_at=CURRENT_TIMESTAMP""",
            (document_id, material_type, material_type,
             json.dumps(clean_tags, ensure_ascii=False), actor["user_id"]),
        )
        row = self.db.fetch_one(
            "SELECT * FROM document_material_metadata WHERE document_id=?", (document_id,)
        ) or {}
        row["tags"] = json.loads(row.pop("tags_json") or "[]")
        affected = (
            list(dict.fromkeys([old_material_type, material_type]))
            if old_material_type != material_type else []
        )
        row["course_id"] = document["course_id"]
        row["affected_material_types"] = affected
        row["rebuild_status"] = "queued" if affected else "not_needed"
        return row

    def rebuild_material_partitions(self, course_id: str,
                                    material_types: list[str]) -> dict[str, Any]:
        """Rebuild only affected partitions from persisted document knowledge nodes."""
        results: dict[str, Any] = {}
        for material_type in dict.fromkeys(material_types):
            if material_type not in MATERIAL_TYPES:
                continue
            latest = self.db.fetch_one(
                """SELECT s.analysis_job_id FROM semantic_analysis_jobs s
                   JOIN course_documents d ON d.document_id=s.document_id
                   LEFT JOIN document_material_metadata m ON m.document_id=d.document_id
                   WHERE s.course_id=? AND COALESCE(m.material_type,'other')=?
                     AND s.status IN ('review_required','completed')
                   ORDER BY s.created_at DESC LIMIT 1""",
                (course_id, material_type),
            ) or {}
            try:
                fallback = self._rebuild_course_outline(
                    course_id, latest.get("analysis_job_id"), use_api=False,
                    material_type=material_type,
                )
                results[material_type] = {
                    "status": "safe_fallback" if fallback else "completed",
                    "message": fallback,
                }
            except Exception as exc:
                results[material_type] = {
                    "status": "failed_keep_previous", "message": str(exc)[:500]
                }
        return {"course_id": course_id, "partitions": results}

    def delete_document(self, actor: dict[str, Any], document_id: str) -> dict[str, Any]:
        """Delete an erroneous, unpublished upload and every derived draft safely."""
        document = self.require_document_access(actor, document_id)
        if actor.get("role") != "teacher" or document["owner_id"] != actor["user_id"]:
            raise PermissionDenied("仅课程教师可以删除资料")
        active = self.db.fetch_one(
            """SELECT 1 ok FROM ingestion_jobs WHERE document_id=? AND status='running'
               UNION SELECT 1 ok FROM semantic_analysis_jobs WHERE document_id=?
               AND status='running' LIMIT 1""",
            (document_id, document_id),
        )
        if active:
            raise ValidationError("资料仍在解析或分析中，请先取消任务后再删除")
        with self.db.connect() as conn:
            conn.execute(
                """UPDATE ingestion_jobs SET status='cancelled',updated_at=CURRENT_TIMESTAMP
                   WHERE document_id=? AND status='queued'""", (document_id,),
            )
            conn.execute(
                """UPDATE semantic_analysis_jobs SET status='cancelled',current_stage='cancelled',
                   updated_at=CURRENT_TIMESTAMP WHERE document_id=?
                   AND status IN ('queued','retry_wait')""", (document_id,),
            )
        published = self.db.fetch_one(
            """SELECT 1 ok FROM knowledge_version_blocks vb
               JOIN document_blocks b USING(block_id) WHERE b.document_id=?
               UNION
               SELECT 1 ok FROM knowledge_version_nodes vn
               JOIN knowledge_nodes n USING(node_id)
               LEFT JOIN knowledge_node_sources s USING(node_id)
               WHERE n.document_id=? OR s.document_id=?
               UNION
               SELECT 1 ok FROM question_bank_version_items qv
               JOIN question_bank_items qi USING(item_id) WHERE qi.document_id=?
               LIMIT 1""",
            (document_id, document_id, document_id, document_id),
        )
        if published:
            raise ValidationError("该资料已被发布版本引用，不能直接删除；请先发布不含该资料的新版本")

        file_rows = self.db.fetch_all(
            """SELECT stored_path FROM document_artifacts WHERE document_id=?
               UNION SELECT source_image_path stored_path FROM document_pages WHERE document_id=?
               UNION SELECT source_image_path stored_path FROM document_blocks WHERE document_id=?""",
            (document_id, document_id, document_id),
        )
        paths = [Path(document["stored_path"]), *[
            Path(row["stored_path"]) for row in file_rows if row.get("stored_path")
        ]]
        with self.db.connect() as conn:
            exclusive_course_nodes = conn.execute(
                """SELECT DISTINCT n.node_id FROM knowledge_nodes n
                   JOIN knowledge_node_sources own ON own.node_id=n.node_id AND own.document_id=?
                   WHERE n.course_id=? AND n.node_scope='course'
                     AND NOT EXISTS (
                         SELECT 1 FROM knowledge_node_sources other
                         WHERE other.node_id=n.node_id AND other.document_id<>?
                     )""",
                (document_id, document["course_id"], document_id),
            ).fetchall()
            conn.executemany(
                "DELETE FROM knowledge_nodes WHERE node_id=?",
                [(row["node_id"],) for row in exclusive_course_nodes],
            )
            conn.execute("DELETE FROM course_documents WHERE document_id=?", (document_id,))

        storage_root = self.campus.storage_dir.resolve()
        warnings: list[str] = []
        for path in dict.fromkeys(paths):
            try:
                resolved = path.resolve()
                if storage_root not in resolved.parents:
                    warnings.append(f"跳过存储目录外文件：{path.name}")
                    continue
                if resolved.is_file():
                    resolved.unlink()
            except OSError as exc:
                warnings.append(f"{path.name}: {exc}")
        assets = (Path(document["stored_path"]).parent / f"{document_id}_assets").resolve()
        try:
            if storage_root in assets.parents and assets.is_dir():
                shutil.rmtree(assets)
        except OSError as exc:
            warnings.append(f"{assets.name}: {exc}")
        ingestion_root = (Path(document["stored_path"]).parent / f"{document_id}_ingestion").resolve()
        try:
            if storage_root in ingestion_root.parents and ingestion_root.is_dir():
                shutil.rmtree(ingestion_root)
        except OSError as exc:
            warnings.append(f"{ingestion_root.name}: {exc}")
        return {"document_id": document_id, "deleted": True, "cleanup_warnings": warnings}

    def delete_documents(self, actor: dict[str, Any], document_ids: list[str]) -> dict[str, Any]:
        unique = list(dict.fromkeys(str(value) for value in document_ids if str(value)))
        deleted: list[str] = []
        failed: list[dict[str, str]] = []
        for document_id in unique:
            try:
                self.delete_document(actor, document_id)
                deleted.append(document_id)
            except (PermissionDenied, ValidationError, NotFound) as exc:
                failed.append({"id": document_id, "message": str(exc)})
        return {"requested": len(unique), "deleted": deleted, "failed": failed}

    def source_file(self, actor: dict[str, Any], document_id: str) -> tuple[dict[str, Any], Path]:
        document = self.require_document_access(actor, document_id)
        source = Path(document["stored_path"]).resolve()
        storage_root = self.campus.storage_dir.resolve()
        if storage_root not in source.parents or not source.is_file():
            raise NotFound("原始资料文件不存在")
        return document, source

    @staticmethod
    def _docx_preview_html(source: Path) -> str:
        """Render a DOCX as escaped, dependency-free review HTML.

        This is deliberately a review preview rather than a format converter:
        it preserves document order, headings, emphasis, lists and tables while
        never emitting macros or arbitrary markup from the uploaded file.
        """
        from docx import Document
        from docx.oxml.table import CT_Tbl
        from docx.oxml.text.paragraph import CT_P
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        document = Document(str(source))

        def paragraph_html(paragraph: Paragraph) -> str:
            pieces: list[str] = []
            for run in paragraph.runs:
                value = html.escape(run.text).replace("\n", "<br>")
                if not value:
                    continue
                if run.bold:
                    value = f"<strong>{value}</strong>"
                if run.italic:
                    value = f"<em>{value}</em>"
                if run.underline:
                    value = f"<u>{value}</u>"
                pieces.append(value)
            content = "".join(pieces) or html.escape(paragraph.text)
            style_name = str(getattr(paragraph.style, "name", "") or "")
            heading = re.match(r"Heading\s+([1-6])", style_name, flags=re.I)
            if heading:
                level = heading.group(1)
                return f"<h{level}>{content}</h{level}>"
            if "List Bullet" in style_name:
                return f'<p class="list bullet">{content}</p>'
            if "List Number" in style_name:
                return f'<p class="list number">{content}</p>'
            return f"<p>{content or '&nbsp;'}</p>"

        def table_html(table: Table) -> str:
            rows: list[str] = []
            for row in table.rows:
                cells = [
                    "<td>" + "<br>".join(
                        html.escape(line) for line in cell.text.splitlines()
                    ) + "</td>"
                    for cell in row.cells
                ]
                rows.append("<tr>" + "".join(cells) + "</tr>")
            return '<div class="table-scroll"><table>' + "".join(rows) + "</table></div>"

        body: list[str] = []
        for child in document.element.body.iterchildren():
            if isinstance(child, CT_P):
                body.append(paragraph_html(Paragraph(child, document)))
            elif isinstance(child, CT_Tbl):
                body.append(table_html(Table(child, document)))
        return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
html{background:#eef1f5}body{box-sizing:border-box;max-width:960px;min-height:100vh;margin:0 auto;padding:38px 46px;background:#fff;color:#202939;font:15px/1.75 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;box-shadow:0 0 20px #bcc4d055}h1,h2,h3,h4,h5,h6{margin:1.35em 0 .55em;line-height:1.35;color:#17233a}p{margin:.5em 0;white-space:pre-wrap}.list{padding-left:1.8em}.bullet:before{content:"• ";margin-left:-1em}.number{counter-increment:item}.number:before{content:counter(item) ". ";margin-left:-1.4em}.table-scroll{max-width:100%;margin:1em 0;overflow:auto}table{width:100%;border-collapse:collapse}td{min-width:7em;padding:7px 9px;border:1px solid #aeb7c6;vertical-align:top;white-space:pre-wrap}@media(max-width:700px){body{padding:20px 16px}}
</style></head><body>""" + "".join(body) + "</body></html>"

    def preview_descriptor(self, actor: dict[str, Any], document_id: str) -> dict[str, Any]:
        document, source = self.source_file(actor, document_id)
        suffix = source.suffix.lower()
        if suffix == ".pdf":
            return {"preview_kind": "pdf", "conversion_status": "ready", "preview_error": ""}
        if suffix in {".txt", ".md"}:
            return {
                "preview_kind": "markdown" if suffix == ".md" else "text",
                "conversion_status": "ready", "preview_error": "",
            }
        if suffix == ".pptx":
            slide_count = int((self.db.fetch_one(
                "SELECT COUNT(*) n FROM presentation_slides WHERE document_id=?",
                (document_id,),
            ) or {}).get("n") or 0)
            if not slide_count:
                slide_count = int((self.db.fetch_one(
                    "SELECT MAX(page_number) n FROM document_blocks WHERE document_id=?",
                    (document_id,),
                ) or {}).get("n") or 1)
            return {
                "preview_kind": "pptx",
                "conversion_status": "ready",
                "preview_error": "",
                "total_pages": slide_count,
            }
        if suffix == ".docx":
            return {
                "preview_kind": "docx", "conversion_status": "ready",
                "preview_error": "", "total_pages": 1,
            }
        artifact = self.db.fetch_one(
            "SELECT * FROM document_artifacts WHERE document_id=? AND artifact_type='preview_pdf'",
            (document_id,),
        )
        if not artifact and suffix == ".docx":
            self._create_office_preview(document)
            artifact = self.db.fetch_one(
                "SELECT * FROM document_artifacts WHERE document_id=? AND artifact_type='preview_pdf'",
                (document_id,),
            )
        if artifact and artifact["status"] == "ready" and Path(artifact["stored_path"]).is_file():
            return {"preview_kind": "pdf", "conversion_status": "ready", "preview_error": ""}
        return {
            "preview_kind": "unavailable",
            "conversion_status": str((artifact or {}).get("status") or "unavailable"),
            "preview_error": str((artifact or {}).get("error_message") or "该文件尚无可用的网页预览"),
        }

    def preview_file(self, actor: dict[str, Any], document_id: str) -> tuple[str, Path | str]:
        document, source = self.source_file(actor, document_id)
        suffix = source.suffix.lower()
        if suffix == ".pdf":
            return "application/pdf", source
        if suffix in {".txt", ".md"}:
            try:
                return "text/markdown" if suffix == ".md" else "text/plain", source.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ValidationError("文本预览仅支持 UTF-8 编码") from exc
        if suffix == ".docx":
            try:
                return "text/html", self._docx_preview_html(source)
            except Exception as exc:
                raise ValidationError(f"Word 文件无法生成网页预览：{exc}") from exc
        artifact = self.db.fetch_one(
            """SELECT * FROM document_artifacts WHERE document_id=? AND artifact_type='preview_pdf'
               AND status='ready'""", (document_id,),
        )
        if not artifact:
            raise NotFound("Office 预览尚未生成")
        preview = Path(artifact["stored_path"]).resolve()
        storage_root = self.campus.storage_dir.resolve()
        if storage_root not in preview.parents or not preview.is_file():
            raise NotFound("Office 预览文件不存在")
        return "application/pdf", preview

    def parser_status(self) -> dict[str, Any]:
        def check(client: Any) -> dict[str, Any]:
            if not client.enabled:
                return {"enabled": False, "status": "not_configured", "url": ""}
            try:
                result = client.health()
                return {**result, "url": client.base_url}
            except Exception as exc:
                return {"enabled": True, "status": "unreachable", "url": client.base_url, "error": str(exc)[:300]}
        settings = get_knowledge_extractor_settings()
        return {
            "mineru": check(self.mineru), "pix2text": check(self.formula),
            "knowledge_extractor": {
                "backend": "builtin", "status": "ok", "uses_torch": False,
                "configured_backend": settings.get("configured_backend", "builtin"),
                "deprecated_backend": bool(settings.get("deprecated_backend")),
                "warning": settings.get("warning", ""),
            },
        }

    def list_student_source_files(self, actor: dict[str, Any], course_id: str) -> list[dict[str, Any]]:
        if actor.get("role") != "student":
            raise PermissionDenied("仅学生可以查询课程原始资料")
        course = self.campus.require_access(course_id, str(actor["user_id"]), "student")
        if course["course_type"] != "shared_course":
            return []
        return self.db.fetch_all(
            """SELECT d.document_id,d.original_name,d.mime_type,d.size_bytes,d.created_at
               FROM course_documents d
               WHERE d.course_id=? AND d.student_file_visible=1
                 AND EXISTS (
                   SELECT 1 FROM document_blocks b
                   JOIN knowledge_version_blocks vb USING(block_id)
                   JOIN knowledge_versions v USING(version_id)
                   WHERE b.document_id=d.document_id AND v.course_id=d.course_id AND v.status='published'
                 )
               ORDER BY d.created_at DESC""",
            (course_id,),
        )

    def cancel_job(self, actor: dict[str, Any], job_id: str) -> dict[str, Any]:
        job = self.get_job(actor, job_id)
        if job["status"] not in {"queued", "running"}:
            raise ValidationError("只有排队中或运行中的任务可以取消")
        self.db.execute(
            "UPDATE ingestion_jobs SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE job_id=?", (job_id,)
        )
        self.db.execute("UPDATE course_documents SET status='cancelled' WHERE document_id=?", (job["document_id"],))
        return self.get_job(actor, job_id)

    def retry_job(self, actor: dict[str, Any], job_id: str) -> dict[str, Any]:
        job = self.get_job(actor, job_id)
        block_count = self.db.fetch_one(
            "SELECT COUNT(*) n FROM document_blocks WHERE document_id=?", (job["document_id"],)
        )
        can_retry_empty_review = (
            job["status"] == "review_required" and int((block_count or {}).get("n") or 0) == 0
        )
        if job["status"] not in {"failed", "cancelled"} and not can_retry_empty_review:
            raise ValidationError("只有失败或已取消的任务可以重试")
        if can_retry_empty_review:
            self.db.execute(
                """UPDATE semantic_analysis_jobs SET status='cancelled',current_stage='cancelled',
                   next_retry_at=NULL,error_message='文档正在重新解析',updated_at=CURRENT_TIMESTAMP
                   WHERE document_id=? AND status IN ('queued','running','retry_wait')""",
                (job["document_id"],),
            )
        self.db.execute(
            """UPDATE ingestion_jobs SET status='queued',progress=0,error_message='',failed_pages=0,
               pipeline_stage='queued',retry_count=retry_count+1,updated_at=CURRENT_TIMESTAMP WHERE job_id=?""", (job_id,)
        )
        self.db.execute("UPDATE course_documents SET status='queued',error_message='' WHERE document_id=?",
                        (job["document_id"],))
        return self.get_job(actor, job_id)

    def reparse_presentation(self, actor: dict[str, Any], job_id: str) -> dict[str, Any]:
        """Rebuild PPT DocumentIR/title candidates without starting semantic AI."""
        job = self.get_job(actor, job_id)
        document = self.db.fetch_one(
            "SELECT stored_path FROM course_documents WHERE document_id=?", (job["document_id"],)
        ) or {}
        if Path(str(document.get("stored_path") or "")).suffix.lower() != ".pptx":
            raise ValidationError("只有 PPTX 资料可以重建标题")
        if job["status"] in {"queued", "running"}:
            raise ValidationError("文档解析仍在进行，暂不能重建 PPT 标题")
        protected = self.db.fetch_one(
            """SELECT 1 ok FROM knowledge_version_blocks vb
               JOIN document_blocks b ON b.block_id=vb.block_id
               WHERE b.document_id=? LIMIT 1""",
            (job["document_id"],),
        ) or self.db.fetch_one(
            """SELECT 1 ok FROM knowledge_version_nodes vn
               JOIN knowledge_node_sources s ON s.node_id=vn.node_id
               WHERE s.document_id=? LIMIT 1""",
            (job["document_id"],),
        )
        if protected:
            raise ValidationError("该 PPT 已进入知识版本，不能直接重建标题；请先创建新资料版本")
        approved = self.db.fetch_one(
            """SELECT COUNT(*) n FROM knowledge_candidates WHERE document_id=?
               AND review_status IN ('APPROVED','MODIFIED')""", (job["document_id"],)
        )
        if int((approved or {}).get("n") or 0):
            raise ValidationError("该 PPT 已有教师批准或修改的候选，为避免覆盖审核结果，不能直接重建")
        self.db.execute(
            """UPDATE semantic_analysis_jobs SET status='cancelled',current_stage='cancelled',
               next_retry_at=NULL,error_message='PPT 标题正在从原文件重新提取',updated_at=CURRENT_TIMESTAMP
               WHERE document_id=? AND status IN ('queued','running','retry_wait')""",
            (job["document_id"],),
        )
        with self.db.connect() as conn:
            conn.execute(
                """DELETE FROM knowledge_nodes WHERE document_id=? AND node_scope='document'
                   AND NOT EXISTS (
                       SELECT 1 FROM knowledge_version_nodes vn
                       WHERE vn.node_id=knowledge_nodes.node_id
                   )""",
                (job["document_id"],),
            )
            conn.execute("DELETE FROM knowledge_candidates WHERE document_id=?", (job["document_id"],))
            conn.execute(
                """UPDATE ingestion_jobs SET status='queued',progress=0,error_message='',failed_pages=0,
                   pipeline_stage='PPT_TITLE_REBUILD',retry_count=retry_count+1,
                   updated_at=CURRENT_TIMESTAMP WHERE job_id=?""", (job_id,),
            )
            conn.execute(
                "UPDATE course_documents SET status='queued',error_message='' WHERE document_id=?",
                (job["document_id"],),
            )
        return self.get_job(actor, job_id)

    def course_health(self, actor: dict[str, Any], course_id: str) -> dict[str, Any]:
        course = self.campus.require_access(course_id, str(actor["user_id"]), "teacher")
        if course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权查看该课程体检单")
        rows = self.db.fetch_all(
            """SELECT p.status,p.parse_method,p.page_type,p.parse_level,p.validation_issues_json,
                      (SELECT COUNT(*) FROM document_blocks b WHERE b.page_id=p.page_id AND b.block_type='formula') formulas,
                      (SELECT COUNT(*) FROM document_blocks b WHERE b.page_id=p.page_id AND b.block_type='table') tables,
                      (SELECT COUNT(*) FROM document_blocks b WHERE b.page_id=p.page_id AND b.verification_status='review_required') pending
               FROM document_pages p JOIN course_documents d USING(document_id) WHERE d.course_id=?""", (course_id,)
        )
        total = len(rows)
        native = sum(1 for row in rows if row["parse_method"] == "native")
        ocr = sum(1 for row in rows if row["parse_method"] != "native")
        version = self.db.fetch_one(
            "SELECT version_number,status,published_at FROM knowledge_versions WHERE course_id=? ORDER BY version_number DESC LIMIT 1",
            (course_id,),
        )
        return {
            "total_pages": total, "native_pages": native, "ocr_pages": ocr,
            "formula_count": sum(int(row["formulas"]) for row in rows),
            "table_count": sum(int(row["tables"]) for row in rows),
            "pending_regions": sum(int(row["pending"]) for row in rows),
            "failed_pages": sum(1 for row in rows if row["status"] == "failed"),
            "suspect_pages": sum(1 for row in rows if row["status"] == "review_required" and row.get("validation_issues_json") not in (None, "", "[]")),
            "page_type_counts": {
                page_type: sum(1 for row in rows if row.get("page_type") == page_type)
                for page_type in sorted({str(row.get("page_type") or "UNKNOWN") for row in rows})
            },
            "parse_level_counts": {
                parse_level: sum(1 for row in rows if row.get("parse_level") == parse_level)
                for parse_level in sorted({str(row.get("parse_level") or "NORMAL") for row in rows})
            },
            "local_processing_ratio": 1.0 if total else 0.0,
            "cloud_model_calls": 0, "cloud_tokens": 0,
            "publication": version or {"version_number": 0, "status": "unpublished", "published_at": None},
        }

    def _sync_approved_source_blocks(self, document_id: str) -> None:
        """Materialize the approved layer from reviewed, immutable source blocks."""
        document = self.db.fetch_one(
            "SELECT stored_path FROM course_documents WHERE document_id=?", (document_id,)
        )
        if not document:
            return
        root = Path(document["stored_path"]).parent / f"{document_id}_ingestion" / "approved"
        root.mkdir(parents=True, exist_ok=True)
        rows = self.db.fetch_all(
            """SELECT block_id,page_index,page_number,bbox_json,markdown,plain_text,latex,
                      visibility_level FROM document_blocks
               WHERE document_id=? AND content_destination='knowledge'
                 AND verification_status IN ('auto_verified','teacher_verified')
               ORDER BY page_index,block_order""",
            (document_id,),
        )
        path = root / "blocks.jsonl"
        with path.open("w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps({
                    "source_block_ids": [row["block_id"]],
                    "page_index": row["page_index"], "page_number": row["page_number"],
                    "bbox": json.loads(row["bbox_json"] or "[]"),
                    "markdown": row["markdown"], "plain_text": row["plain_text"],
                    "latex": row["latex"], "visibility_level": row["visibility_level"],
                }, ensure_ascii=False) + "\n")
        candidates = self.db.fetch_all(
            """SELECT candidate_id,title,knowledge_type,source_block_ids_json,page_start,page_end,
                      chapter_path_json,markdown_content,teacher_revision,confidence,review_status
               FROM knowledge_candidates WHERE document_id=? AND review_status='APPROVED'
               ORDER BY page_start,candidate_id""", (document_id,),
        )
        points_path = root / "knowledge_points.jsonl"
        with points_path.open("w", encoding="utf-8") as stream:
            for candidate in candidates:
                try:
                    source_ids = json.loads(candidate["source_block_ids_json"] or "[]")
                except json.JSONDecodeError:
                    source_ids = []
                stream.write(json.dumps({
                    "candidate_id": candidate["candidate_id"],
                    "title": candidate["title"],
                    "knowledge_type": candidate["knowledge_type"],
                    "source_block_ids": source_ids,
                    "page_start": candidate["page_start"], "page_end": candidate["page_end"],
                    "chapter_path": json.loads(candidate.get("chapter_path_json") or "[]"),
                    "markdown": candidate["teacher_revision"] or candidate["markdown_content"],
                    "confidence": candidate["confidence"],
                    "review_status": candidate["review_status"],
                }, ensure_ascii=False) + "\n")

    def _enforce_navigation_exclusion(self, document_id: str) -> None:
        """Keep routed navigation/decoration blocks out of semantic extraction."""
        self.db.execute(
            """UPDATE document_blocks SET content_destination='excluded',semantic_role='navigation',
                      analysis_reason='页面路由标记为导航或非知识内容',updated_at=CURRENT_TIMESTAMP
               WHERE document_id=? AND include_as_knowledge=0""",
            (document_id,),
        )

    def list_blocks(self, actor: dict[str, Any], document_id: str, *, limit: int = 100,
                    offset: int = 0, page_number: int | None = None) -> list[dict[str, Any]]:
        doc = self.db.fetch_one("SELECT * FROM course_documents WHERE document_id=?", (document_id,))
        if not doc:
            raise NotFound("资料不存在")
        course = self.campus.require_access(doc["course_id"], str(actor["user_id"]), "teacher")
        if course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权审核该资料")
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        if page_number is None:
            rows = self.db.fetch_all(
                """SELECT * FROM document_blocks WHERE document_id=?
                   ORDER BY page_number,block_order LIMIT ? OFFSET ?""",
                (document_id, limit, offset),
            )
        else:
            rows = self.db.fetch_all(
                """SELECT * FROM document_blocks WHERE document_id=? AND page_number=?
                   ORDER BY block_order LIMIT ? OFFSET ?""",
                (document_id, max(1, int(page_number)), limit, offset),
            )
        for row in rows:
            row["bbox"] = json.loads(row.pop("bbox_json"))
            row["search_aliases"] = json.loads(row.pop("search_aliases_json"))
        return rows

    def count_blocks(self, actor: dict[str, Any], document_id: str,
                     page_number: int | None = None) -> int:
        doc = self.db.fetch_one("SELECT course_id FROM course_documents WHERE document_id=?", (document_id,))
        if not doc:
            raise NotFound("资料不存在")
        course = self.campus.require_access(doc["course_id"], str(actor["user_id"]), "teacher")
        if course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权审核该资料")
        if page_number is None:
            row = self.db.fetch_one("SELECT COUNT(*) n FROM document_blocks WHERE document_id=?", (document_id,))
        else:
            row = self.db.fetch_one(
                "SELECT COUNT(*) n FROM document_blocks WHERE document_id=? AND page_number=?",
                (document_id, max(1, int(page_number))),
            )
        return int((row or {}).get("n") or 0)

    def review_block(self, actor: dict[str, Any], block_id: str, *, markdown: str, plain_text: str,
                     latex: str, visibility_level: str, accepted: bool) -> dict[str, Any]:
        block = self.db.fetch_one(
            """SELECT b.*,d.course_id FROM document_blocks b JOIN course_documents d USING(document_id)
               WHERE block_id=?""", (block_id,),
        )
        if not block:
            raise NotFound("知识块不存在")
        course = self.campus.require_access(block["course_id"], str(actor["user_id"]), "teacher")
        if course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权审核该知识块")
        if visibility_level not in {"PUBLIC", "GUIDANCE", "ASSESSMENT", "VAULT"}:
            raise ValidationError("知识能力域不合法")
        status = "teacher_verified" if accepted else "rejected"
        destination = "knowledge" if accepted and bool(block.get("include_as_knowledge", 1)) else "excluded"
        self.db.execute(
            """UPDATE document_blocks SET markdown=?,plain_text=?,latex=?,visibility_level=?,verification_status=?,
               content_destination=?,reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,
               updated_at=CURRENT_TIMESTAMP WHERE block_id=?""",
            (markdown, plain_text, latex, visibility_level, status, destination, actor["user_id"], block_id),
        )
        self._sync_approved_source_blocks(block["document_id"])
        return self.db.fetch_one("SELECT * FROM document_blocks WHERE block_id=?", (block_id,)) or {}

    def update_classification(self, actor: dict[str, Any], block_id: str, *, destination: str,
                              semantic_role: str = "", question_group_key: str = "",
                              reason: str = "教师手动调整") -> dict[str, Any]:
        block = self.db.fetch_one(
            """SELECT b.*,d.course_id FROM document_blocks b JOIN course_documents d USING(document_id)
               WHERE block_id=?""", (block_id,),
        )
        if not block:
            raise NotFound("内容块不存在")
        course = self.campus.require_access(block["course_id"], str(actor["user_id"]), "teacher")
        if course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权调整该内容块")
        if destination not in {"knowledge", "question_bank", "excluded", "unclassified"}:
            raise ValidationError("内容去向无效")
        if destination == "knowledge" and block["block_type"] in {"image", "table"}:
            raise ValidationError("图片和表格不能直接进入知识库，可进入习题附件或排除")
        if destination == "knowledge" and not bool(block.get("include_as_knowledge", 1)):
            raise ValidationError("导航、封面和装饰页不能进入知识库")
        group = question_group_key.strip()[:100]
        if destination == "question_bank" and not group:
            group = f"page-{block['page_number'] or 1}-item-{block['block_order']}"
        self.db.execute(
            """UPDATE document_blocks SET content_destination=?,semantic_role=?,question_group_key=?,
               analysis_confidence=1,analysis_reason=?,verification_status='review_required',
               updated_at=CURRENT_TIMESTAMP WHERE block_id=?""",
            (destination, semantic_role.strip()[:64], group, reason[:500], block_id),
        )
        self.db.execute("DELETE FROM question_bank_attachments WHERE block_id=?", (block_id,))
        self._rebuild_question_drafts(block["document_id"], block["course_id"])
        self._sync_approved_source_blocks(block["document_id"])
        return self.db.fetch_one("SELECT * FROM document_blocks WHERE block_id=?", (block_id,)) or {}

    def get_classification(self, actor: dict[str, Any], block_id: str) -> dict[str, Any]:
        block = self.db.fetch_one(
            """SELECT b.*,d.course_id FROM document_blocks b JOIN course_documents d USING(document_id)
               WHERE block_id=?""", (block_id,),
        )
        if not block:
            raise NotFound("内容块不存在")
        course = self.campus.require_access(block["course_id"], str(actor["user_id"]), "teacher")
        if course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权查看该内容块")
        return block

    def list_question_bank(self, actor: dict[str, Any], course_id: str) -> list[dict[str, Any]]:
        course = self.campus.require_access(course_id, str(actor["user_id"]), "teacher")
        if course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权查看该课程习题库")
        items = self.db.fetch_all(
            """SELECT * FROM question_bank_items
               WHERE course_id=? AND source_kind='teacher_template'
               ORDER BY CASE WHEN status='draft' AND (
                   answer_markdown='' OR recognition_confidence<0.7
                   OR recognition_notes_json NOT IN ('','[]')
               ) THEN 0 ELSE 1 END,created_at""", (course_id,),
        )
        for item in items:
            item["knowledge_points"] = json.loads(item.pop("knowledge_points_json") or "[]")
            item["source_pages"] = json.loads(item.pop("source_pages_json") or "[]")
            item["options"] = json.loads(item.pop("options_json") or "[]")
            item["correct_answer"] = json.loads(item.pop("correct_answer_json") or '""')
            item["recognition_notes"] = json.loads(item.pop("recognition_notes_json") or "[]")
            attachments = self.db.fetch_all(
                "SELECT * FROM question_bank_attachments WHERE item_id=? ORDER BY page_number", (item["item_id"],),
            )
            for attachment in attachments:
                attachment["bbox"] = json.loads(attachment.pop("bbox_json") or "[]")
            item["attachments"] = attachments
        return items

    def review_question(self, actor: dict[str, Any], item_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        item = self.db.fetch_one("SELECT * FROM question_bank_items WHERE item_id=?", (item_id,))
        if not item:
            raise NotFound("习题不存在")
        course = self.campus.require_access(item["course_id"], str(actor["user_id"]), "teacher")
        if course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权审核该习题")
        status = str(updates.get("status", item["status"]))
        if status not in {"draft", "approved", "rejected"}:
            raise ValidationError("习题状态无效")
        question_type = str(updates.get("question_type", item["question_type"]))[:40]
        stem = str(updates.get("stem_markdown", item["stem_markdown"])).strip()
        answer_markdown = str(updates.get("answer_markdown", item["answer_markdown"])).strip()
        options = updates.get("options", json.loads(item.get("options_json") or "[]"))
        correct_answer = updates.get("correct_answer")
        if question_type == "true_false":
            compact_answer = re.sub(r"\s+", "", answer_markdown).lower()
            if compact_answer in {"y", "n", "t", "f"}:
                answer_markdown = correct_answer = compact_answer.upper()
            elif compact_answer in {"对", "正确", "是", "√", "true", "yes", "1"}:
                answer_markdown = correct_answer = "T"
            elif compact_answer in {"错", "错误", "否", "×", "false", "no", "0"}:
                answer_markdown = correct_answer = "F"
            elif status == "approved":
                raise ValidationError("判断题标准答案必须是 T 或 F")
        if correct_answer is None:
            correct_answer = (
                list(dict.fromkeys(re.findall(r"[A-O]", answer_markdown.upper())))
                if question_type == "multiple_choice" else answer_markdown
            )
        if status == "approved":
            if not stem or not answer_markdown:
                raise ValidationError("批准前必须填写题干和标准答案")
            if question_type in {"single_choice", "multiple_choice"} and len(options) < 2:
                raise ValidationError("批准选择题前至少需要两个选项")
            option_keys = {str(option.get("key", "")).upper() for option in options}
            answer_keys = correct_answer if isinstance(correct_answer, list) else [correct_answer]
            if question_type in {"single_choice", "multiple_choice"} and any(
                str(answer).upper() not in option_keys for answer in answer_keys
            ):
                raise ValidationError("标准答案必须对应现有选项")
        values = (
            question_type,
            stem,
            answer_markdown,
            str(updates.get("explanation_markdown", item["explanation_markdown"])),
            json.dumps(updates.get("knowledge_points", json.loads(item["knowledge_points_json"])), ensure_ascii=False),
            json.dumps(options, ensure_ascii=False),
            json.dumps(correct_answer, ensure_ascii=False),
            str(updates.get("difficulty", item.get("difficulty") or ""))[:40],
            updates.get("duration_seconds", item.get("duration_seconds")),
            updates.get("knowledge_node_id", item.get("knowledge_node_id")), status, actor["user_id"], item_id,
        )
        self.db.execute(
            """UPDATE question_bank_items SET question_type=?,stem_markdown=?,answer_markdown=?,
               explanation_markdown=?,knowledge_points_json=?,options_json=?,correct_answer_json=?,
               difficulty=?,duration_seconds=?,knowledge_node_id=?,status=?,reviewed_by=?,
               reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE item_id=?""", values,
        )
        return next(row for row in self.list_question_bank(actor, item["course_id"]) if row["item_id"] == item_id)

    def publish_question_bank(self, actor: dict[str, Any], course_id: str, *,
                              folder_id: str | None = None) -> dict[str, Any]:
        course = self.campus.require_access(course_id, str(actor["user_id"]), "teacher")
        if course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权发布该课程习题库")
        if folder_id:
            folder = self.db.fetch_one(
                "SELECT * FROM question_bank_folders WHERE folder_id=? AND course_id=?",
                (folder_id, course_id),
            )
            if not folder:
                raise ValidationError("题库文件夹不存在")
            items = self.db.fetch_all(
                """SELECT item_id FROM question_bank_items WHERE course_id=? AND folder_id=?
                   AND status='approved' AND source_kind='teacher_template'""",
                (course_id, folder_id),
            )
        else:
            items = self.db.fetch_all(
                """SELECT item_id FROM question_bank_items WHERE course_id=? AND folder_id IS NULL
                   AND status='approved' AND source_kind='teacher_template'""",
                (course_id,),
            )
        if not items:
            raise ValidationError("没有已批准的习题可发布")
        current = self.db.fetch_one(
            "SELECT COALESCE(MAX(version_number),0) n FROM question_bank_versions WHERE course_id=?", (course_id,),
        )
        version_id = f"qbv_{uuid.uuid4().hex}"
        with self.db.connect() as conn:
            conn.execute(
                """UPDATE question_bank_versions SET status='superseded'
                   WHERE course_id=? AND status='published'
                     AND (folder_id=? OR (folder_id IS NULL AND ? IS NULL))""",
                (course_id, folder_id, folder_id),
            )
            conn.execute(
                """INSERT INTO question_bank_versions(
                       version_id,course_id,version_number,status,created_by,folder_id
                   ) VALUES(?,?,?,'published',?,?)""",
                (version_id, course_id, int(current["n"]) + 1, actor["user_id"], folder_id),
            )
            conn.executemany("INSERT INTO question_bank_version_items(version_id,item_id) VALUES(?,?)",
                             [(version_id, row["item_id"]) for row in items])
            conn.execute(
                """UPDATE question_bank_imports SET status='published',updated_at=CURRENT_TIMESTAMP
                   WHERE course_id=? AND import_id IN (
                       SELECT DISTINCT import_id FROM question_bank_items
                       WHERE course_id=? AND status='approved' AND import_id IS NOT NULL
                   )""",
                (course_id, course_id),
            )
            if folder_id:
                conn.execute(
                    """UPDATE question_bank_folders SET status='published',
                       updated_at=CURRENT_TIMESTAMP WHERE folder_id=?""",
                    (folder_id,),
                )
        return self.db.fetch_one("SELECT * FROM question_bank_versions WHERE version_id=?", (version_id,)) or {}

    def _outline(self, actor: dict[str, Any], course_id: str, *, document_id: str | None,
                 scope: str, material_type: str | None = None,
                 class_id: str | None = None) -> dict[str, Any]:
        course = self.campus.require_access(course_id, str(actor["user_id"]), "teacher")
        if course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权查看该知识目录")
        if material_type is not None and material_type not in MATERIAL_TYPES:
            raise ValidationError("资料用途类型无效")
        self._refresh_teaching_archive_domains(course_id, document_id)
        if scope == "course":
            self._ensure_partitioned_course_outline(course_id)
        self._sync_knowledge_class_scopes(course_id)
        if class_id and class_id != "course_wide":
            if not self.db.fetch_one(
                "SELECT 1 ok FROM classes WHERE class_id=? AND course_id=? AND teacher_id=?",
                (class_id, course_id, actor["user_id"]),
            ):
                raise ValidationError("教学等级/班级筛选范围无效")
        condition = "course_id=? AND node_scope=? AND content_domain='knowledge'"
        params: tuple[Any, ...] = (course_id, scope)
        if document_id:
            condition += " AND document_id=?"
            params += (document_id,)
            latest = self.db.fetch_one(
                """SELECT analysis_job_id FROM semantic_analysis_jobs WHERE document_id=?
                   AND status IN ('review_required','completed') ORDER BY created_at DESC LIMIT 1""",
                (document_id,),
            )
        else:
            latest = None
            condition += """ AND (
                generation_id IN (SELECT generation_id FROM course_outline_generations WHERE course_id=? AND status='current')
                OR (generation_id IS NULL AND NOT EXISTS (
                    SELECT 1 FROM course_outline_generations WHERE course_id=? AND status='current'
                ))
            )"""
            params += (course_id, course_id)
            if material_type:
                condition += " AND material_type=?"
                params += (material_type,)
        if latest and scope == "document":
            condition += " AND (analysis_job_id=? OR analysis_job_id IS NULL)"
            params += (latest["analysis_job_id"],)
        if class_id == "course_wide":
            condition += " AND NOT EXISTS (SELECT 1 FROM knowledge_node_class_scopes ks WHERE ks.node_id=knowledge_nodes.node_id)"
        elif class_id:
            condition += """ AND (
                node_type!='knowledge_point'
                OR NOT EXISTS (SELECT 1 FROM knowledge_node_class_scopes ks WHERE ks.node_id=knowledge_nodes.node_id)
                OR EXISTS (SELECT 1 FROM knowledge_node_class_scopes ks
                           WHERE ks.node_id=knowledge_nodes.node_id AND ks.class_id=?))"""
            params += (class_id,)
        rows = self.db.fetch_all(
            f"SELECT * FROM knowledge_nodes WHERE {condition} AND status!='rejected' ORDER BY sort_order,node_id", params,
        )
        row_map = {row["node_id"]: row for row in rows}
        visible_ids: set[str] = set()
        for row in rows:
            if row["node_type"] != "knowledge_point":
                continue
            current: dict[str, Any] | None = row
            while current and current["node_id"] not in visible_ids:
                visible_ids.add(current["node_id"])
                current = row_map.get(current.get("parent_id"))
        rows = [row for row in rows if row["node_id"] in visible_ids]
        for row in rows:
            row.pop("summary", None)
            row["keywords"] = json.loads(row.pop("keywords_json") or "[]")
            row["source_pages"] = json.loads(row.pop("source_pages_json") or "[]")
            row["sources"] = self.db.fetch_all(
                """SELECT s.block_id,s.document_id,s.page_number,s.bbox_json,d.original_name
                   FROM knowledge_node_sources s JOIN course_documents d USING(document_id)
                   WHERE s.node_id=? ORDER BY s.page_number""", (row["node_id"],),
            )
            for source in row["sources"]:
                source["bbox"] = json.loads(source.pop("bbox_json") or "[]")
            row["teaching_scopes"] = self.db.fetch_all(
                """SELECT ks.class_id,ks.assignment_source,cl.class_name,cl.class_variant,
                          cl.teaching_time_slot,t.academic_year,t.teaching_period,t.term_name
                   FROM knowledge_node_class_scopes ks JOIN classes cl USING(class_id)
                   JOIN terms t USING(term_id) WHERE ks.node_id=?
                   ORDER BY t.academic_year,t.teaching_period,cl.class_variant,cl.class_name""",
                (row["node_id"],),
            ) if row["node_type"] == "knowledge_point" else []
            row["class_ids"] = [item["class_id"] for item in row["teaching_scopes"]]
            row["is_course_wide"] = row["node_type"] == "knowledge_point" and not row["class_ids"]
        partitions: list[dict[str, Any]] = []
        if scope == "course":
            partition_rows = self.db.fetch_all(
                """SELECT g.material_type,g.generation_id,g.fallback_reason,g.completed_at,
                          (SELECT COUNT(*) FROM knowledge_nodes n
                           WHERE n.generation_id=g.generation_id AND n.node_type='knowledge_point'
                             AND n.status!='rejected' AND n.content_domain='knowledge') knowledge_point_count,
                          (SELECT COUNT(*) FROM knowledge_nodes n
                           WHERE n.generation_id=g.generation_id AND n.node_type='knowledge_point'
                             AND n.status='draft' AND n.content_domain='knowledge') pending_review_count,
                          (SELECT COUNT(*) FROM course_documents d
                           LEFT JOIN document_material_metadata m ON m.document_id=d.document_id
                           WHERE d.course_id=g.course_id AND COALESCE(m.material_type,'other')=g.material_type) document_count,
                          (SELECT COUNT(*) FROM course_documents d
                           LEFT JOIN document_material_metadata m ON m.document_id=d.document_id
                           WHERE d.course_id=g.course_id AND COALESCE(m.material_type,'other')=g.material_type
                             AND COALESCE(m.classification_status,'suggested')!='confirmed') unconfirmed_document_count
                   FROM course_outline_generations g
                   WHERE g.course_id=? AND g.status='current'""",
                (course_id,),
            )
            rank = {value: index for index, value in enumerate(MATERIAL_ORDER)}
            partitions = sorted(({
                **row, "label": MATERIAL_LABELS.get(str(row["material_type"]), "其他"),
                "rebuild_status": "safe_fallback" if row.get("fallback_reason") else "completed",
            } for row in partition_rows if int(row.get("knowledge_point_count") or 0) > 0),
                key=lambda row: rank.get(str(row["material_type"]), len(rank)))
        return {
            "nodes": rows,
            "relations": self.list_relations(actor, course_id, material_type=material_type),
            "partitions": partitions,
            "teaching_levels": self.db.fetch_all(
                """SELECT cl.class_id,cl.class_name,cl.class_variant,cl.teaching_time_slot,
                          t.academic_year,t.teaching_period,t.term_name
                   FROM classes cl JOIN terms t USING(term_id)
                   WHERE cl.course_id=? AND cl.teacher_id=? AND cl.status='active'
                   ORDER BY t.academic_year DESC,t.teaching_period,cl.class_variant,cl.class_name""",
                (course_id, actor["user_id"]),
            ),
        }

    def document_outline(
        self, actor: dict[str, Any], document_id: str, *, class_id: str | None = None
    ) -> dict[str, Any]:
        document = self.require_document_access(actor, document_id)
        return self._outline(
            actor, document["course_id"], document_id=document_id,
            scope="document", class_id=class_id,
        )

    def course_outline(self, actor: dict[str, Any], course_id: str, *,
                       material_type: str | None = None,
                       class_id: str | None = None) -> dict[str, Any]:
        return self._outline(
            actor, course_id, document_id=None, scope="course",
            material_type=material_type, class_id=class_id,
        )

    def _require_node(self, actor: dict[str, Any], node_id: str) -> dict[str, Any]:
        node = self.db.fetch_one("SELECT * FROM knowledge_nodes WHERE node_id=?", (node_id,))
        if not node:
            raise NotFound("知识目录节点不存在")
        course = self.campus.require_access(node["course_id"], str(actor["user_id"]), "teacher")
        if course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权修改该知识目录")
        return node

    @staticmethod
    def _put_nodes_in_trash(conn: Any, rows: list[Any], actor_id: str, *,
                            reason: str, action_type: str,
                            batch_id: str | None = None) -> str:
        trash_batch_id = batch_id or f"trash_{uuid.uuid4().hex}"
        conn.executemany(
            """INSERT INTO knowledge_node_trash(
                   node_id,course_id,trash_batch_id,original_parent_id,reason,action_type,trashed_by
               ) VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(node_id) DO UPDATE SET
                   trash_batch_id=excluded.trash_batch_id,
                   original_parent_id=excluded.original_parent_id,
                   reason=excluded.reason,action_type=excluded.action_type,
                   trashed_by=excluded.trashed_by,trashed_at=CURRENT_TIMESTAMP""",
            [(
                row["node_id"], row["course_id"], trash_batch_id, row["parent_id"],
                reason[:500], action_type, actor_id,
            ) for row in rows],
        )
        return trash_batch_id

    def update_node(self, actor: dict[str, Any], node_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        node = self._require_node(actor, node_id)
        status = str(updates.get("status", node["status"]))
        if status not in {"draft", "approved", "rejected"}:
            raise ValidationError("知识节点审核状态无效")
        parent_id = updates.get("parent_id", node["parent_id"])
        if parent_id:
            parent = self._require_node(actor, str(parent_id))
            if (
                parent["course_id"] != node["course_id"]
                or parent["node_scope"] != node["node_scope"]
                or parent.get("material_type") != node.get("material_type")
                or parent.get("generation_id") != node.get("generation_id")
            ):
                raise ValidationError("父节点必须位于同一课程和同一目录范围")
        with self.db.connect() as conn:
            conn.execute(
                """UPDATE knowledge_nodes SET title=?,summary='',markdown=?,keywords_json=?,parent_id=?,sort_order=?,
                   status=?,reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE node_id=?""",
                (self._clean_title(updates.get("title", node["title"]), node["title"]),
                 str(updates.get("markdown", node["markdown"])),
                 json.dumps(updates.get("keywords", json.loads(node["keywords_json"] or "[]")), ensure_ascii=False),
                 parent_id, int(updates.get("sort_order", node["sort_order"])), status, actor["user_id"], node_id),
            )
            affected_ids = [node_id]
            if status in {"approved", "rejected"} and node["node_type"] in {"chapter", "section"}:
                affected_rows = conn.execute(
                    """WITH RECURSIVE descendants(node_id) AS (
                           SELECT node_id FROM knowledge_nodes WHERE node_id=?
                           UNION ALL
                           SELECT n.node_id FROM knowledge_nodes n JOIN descendants d ON n.parent_id=d.node_id
                       ) SELECT n.* FROM knowledge_nodes n JOIN descendants d USING(node_id)""", (node_id,),
                ).fetchall()
                affected_ids = [row["node_id"] for row in affected_rows]
                conn.executemany(
                    """UPDATE knowledge_nodes SET status=?,reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,
                       updated_at=CURRENT_TIMESTAMP WHERE node_id=?""",
                    [(status, actor["user_id"], value) for value in affected_ids],
                )
            else:
                affected_rows = conn.execute(
                    "SELECT * FROM knowledge_nodes WHERE node_id=?", (node_id,)
                ).fetchall()
            if status == "rejected":
                self._put_nodes_in_trash(
                    conn, affected_rows, str(actor["user_id"]),
                    reason=str(updates.get("reason") or "教师审核未通过"),
                    action_type="teacher_rejected",
                )
            else:
                placeholders = ",".join("?" for _ in affected_ids)
                conn.execute(
                    f"DELETE FROM knowledge_node_trash WHERE node_id IN ({placeholders})",
                    tuple(affected_ids),
                )
            placeholders = ",".join("?" for _ in affected_ids)
            verification = "teacher_verified" if status == "approved" else (
                "rejected" if status == "rejected" else "review_required"
            )
            conn.execute(
                f"""UPDATE document_blocks SET verification_status=?,reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP
                    WHERE block_id IN (SELECT block_id FROM knowledge_node_sources
                    WHERE node_id IN ({placeholders}))""",
                (verification, actor["user_id"], *affected_ids),
            )
        updated = self.db.fetch_one("SELECT * FROM knowledge_nodes WHERE node_id=?", (node_id,)) or {}
        if "class_ids" in updates:
            scope_ids = affected_ids
            if node["node_type"] in {"chapter", "section"}:
                scope_ids = [
                    str(row["node_id"]) for row in self.db.fetch_all(
                        """WITH RECURSIVE descendants(node_id) AS (
                               SELECT node_id FROM knowledge_nodes WHERE node_id=?
                               UNION ALL SELECT n.node_id FROM knowledge_nodes n
                               JOIN descendants d ON n.parent_id=d.node_id
                           ) SELECT node_id FROM descendants""",
                        (node_id,),
                    )
                ]
            self._set_node_class_scopes(
                actor, scope_ids, list(updates.get("class_ids") or [])
            )
        if node["node_scope"] == "document":
            self._sync_candidates_for_nodes(affected_ids, status, str(actor["user_id"]))
            if node.get("document_id"):
                self._sync_approved_source_blocks(str(node["document_id"]))
        updated.pop("summary", None)
        return updated

    def approve_nodes_batch(
        self, actor: dict[str, Any], node_ids: list[str]
    ) -> dict[str, Any]:
        """Atomically approve checked document-tree branches and their leaves."""
        unique = list(dict.fromkeys(str(value) for value in node_ids if str(value)))
        if not unique:
            raise ValidationError("至少选择一个知识节点")
        selected = [self._require_node(actor, node_id) for node_id in unique]
        first = selected[0]
        if first["node_scope"] != "document":
            raise ValidationError("批量批准仅用于文档独立目录")
        if any(
            node["node_scope"] != "document"
            or node.get("document_id") != first.get("document_id")
            or node["course_id"] != first["course_id"]
            for node in selected
        ):
            raise ValidationError("只能批量批准同一文档独立目录中的节点")
        expanded: dict[str, dict[str, Any]] = {}
        for node in selected:
            for row in self.db.fetch_all(
                """WITH RECURSIVE descendants(node_id) AS (
                       SELECT node_id FROM knowledge_nodes WHERE node_id=?
                       UNION ALL
                       SELECT n.node_id FROM knowledge_nodes n
                       JOIN descendants d ON n.parent_id=d.node_id
                   ) SELECT n.* FROM knowledge_nodes n JOIN descendants d USING(node_id)
                   WHERE n.node_scope='document'""",
                (node["node_id"],),
            ):
                expanded[str(row["node_id"])] = row
        if not expanded:
            raise ValidationError("勾选范围没有可批准的知识节点")
        all_ids = list(expanded)
        leaf_ids = [
            node_id for node_id, row in expanded.items()
            if row["node_type"] == "knowledge_point"
        ]
        placeholders = ",".join("?" for _ in all_ids)
        with self.db.connect() as conn:
            conn.execute(
                f"""UPDATE knowledge_nodes SET status='approved',reviewed_by=?,
                    reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                    WHERE node_id IN ({placeholders})""",
                (actor["user_id"], *all_ids),
            )
            conn.execute(
                f"DELETE FROM knowledge_node_trash WHERE node_id IN ({placeholders})",
                tuple(all_ids),
            )
            conn.execute(
                f"""UPDATE document_blocks SET verification_status='teacher_verified',
                    reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                    WHERE block_id IN (SELECT block_id FROM knowledge_node_sources
                                       WHERE node_id IN ({placeholders}))""",
                (actor["user_id"], *all_ids),
            )
        candidate_ids = self._sync_candidates_for_nodes(
            leaf_ids, "approved", str(actor["user_id"]),
        )
        self._sync_approved_source_blocks(str(first["document_id"]))
        return {
            "document_id": first["document_id"],
            "approved_node_ids": all_ids,
            "approved_leaf_count": len(leaf_ids),
            "synced_candidate_ids": candidate_ids,
        }

    def move_nodes(
        self,
        actor: dict[str, Any],
        node_ids: list[str],
        target_parent_id: str | None,
        target_index: int,
    ) -> list[dict[str, Any]]:
        """Move same-level knowledge nodes with folder-like semantics."""
        unique = list(dict.fromkeys(str(value) for value in node_ids if str(value)))
        if not unique:
            raise ValidationError("至少选择一个知识节点")
        nodes = [self._require_node(actor, node_id) for node_id in unique]
        first = nodes[0]
        if any(
            node["course_id"] != first["course_id"]
            or node["node_scope"] != first["node_scope"]
            or node["node_type"] != first["node_type"]
            or node.get("material_type") != first.get("material_type")
            or node.get("generation_id") != first.get("generation_id")
            for node in nodes
        ):
            raise ValidationError("只能批量移动同一目录范围、同一层级的节点")

        node_type = str(first["node_type"])
        if target_parent_id:
            parent = self._require_node(actor, target_parent_id)
            if (
                parent["course_id"] != first["course_id"]
                or parent["node_scope"] != first["node_scope"]
                or parent.get("material_type") != first.get("material_type")
                or parent.get("generation_id") != first.get("generation_id")
                or parent["status"] == "rejected"
            ):
                raise ValidationError("目标文件夹必须位于同一目录范围和材料分区")
            if parent["node_type"] == "knowledge_point":
                raise ValidationError("知识点不能作为文件夹")
            descendant_ids = {
                str(row["node_id"])
                for node_id in unique
                for row in self.db.fetch_all(
                    """WITH RECURSIVE descendants(node_id) AS (
                           SELECT node_id FROM knowledge_nodes WHERE node_id=?
                           UNION ALL
                           SELECT n.node_id FROM knowledge_nodes n
                           JOIN descendants d ON n.parent_id=d.node_id
                       ) SELECT node_id FROM descendants""",
                    (node_id,),
                )
            }
            if str(target_parent_id) in descendant_ids:
                raise ValidationError("不能把文件夹移动到自身或其子目录中")
        elif node_type != "chapter":
            raise ValidationError("只有一级标题文件夹可以移动到知识树根目录")

        siblings = self.db.fetch_all(
            """SELECT * FROM knowledge_nodes WHERE course_id=? AND node_scope=?
               AND material_type=?
               AND ((generation_id IS NULL AND ? IS NULL) OR generation_id=?)
               AND status!='rejected'
               AND ((parent_id IS NULL AND ? IS NULL) OR parent_id=?)
               ORDER BY sort_order,node_id""",
            (
                first["course_id"], first["node_scope"],
                first.get("material_type") or "other",
                first.get("generation_id"), first.get("generation_id"),
                target_parent_id, target_parent_id,
            ),
        )
        moving_ids = set(unique)
        remaining = [row for row in siblings if row["node_id"] not in moving_ids]
        # Preserve the caller's visible tree order for a checked multi-drag.
        moving = nodes
        index = max(0, min(int(target_index), len(remaining)))
        ordered = [*remaining[:index], *moving, *remaining[index:]]
        with self.db.connect() as conn:
            conn.executemany(
                """UPDATE knowledge_nodes SET parent_id=?,sort_order=?,reviewed_by=?,
                   reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE node_id=?""",
                [
                    (target_parent_id, (position + 1) * 10, actor["user_id"], row["node_id"])
                    for position, row in enumerate(ordered)
                ],
            )
        return [
            self.db.fetch_one("SELECT * FROM knowledge_nodes WHERE node_id=?", (node_id,)) or {}
            for node_id in unique
        ]

    def move_nodes_as_visible_siblings(
        self,
        actor: dict[str, Any],
        node_ids: list[str],
        target_node_id: str,
        position: str,
    ) -> dict[str, Any]:
        """Promote child branches to the target's visible level.

        The persisted outline keeps the strict chapter -> section -> point
        hierarchy. When, for example, a point is dragged beside a section, an
        empty section wrapper is inserted around the point. The compact tree
        hides that wrapper, so the teacher sees the requested peer branches
        without corrupting the semantic hierarchy.
        """
        unique = list(dict.fromkeys(str(value) for value in node_ids if str(value)))
        if not unique:
            raise ValidationError("至少选择一个知识节点")
        if position not in {"before", "after"}:
            raise ValidationError("并列移动位置只能是上方或下方")
        nodes = [self._require_node(actor, node_id) for node_id in unique]
        target = self._require_node(actor, str(target_node_id))
        first = nodes[0]
        if target["node_id"] in unique:
            raise ValidationError("不能把节点拖到自身旁边")
        if any(
            node["course_id"] != first["course_id"]
            or node["node_scope"] != first["node_scope"]
            or node["node_type"] != first["node_type"]
            or node.get("material_type") != first.get("material_type")
            or node.get("generation_id") != first.get("generation_id")
            for node in nodes
        ):
            raise ValidationError("只能批量提升同一目录范围、同一层级的节点")
        if (
            target["course_id"] != first["course_id"]
            or target["node_scope"] != first["node_scope"]
            or target.get("material_type") != first.get("material_type")
            or target.get("generation_id") != first.get("generation_id")
            or target["status"] == "rejected"
        ):
            raise ValidationError("目标节点必须位于同一材料分区")

        type_order = ["chapter", "section", "knowledge_point"]
        moving_rank = type_order.index(str(first["node_type"]))
        target_rank = type_order.index(str(target["node_type"]))
        if moving_rank <= target_rank:
            raise ValidationError("该操作只用于把子项提升到父级旁边")

        target_parent_id = target.get("parent_id")
        siblings = self.db.fetch_all(
            """SELECT * FROM knowledge_nodes WHERE course_id=? AND node_scope=?
               AND node_type=? AND material_type=?
               AND ((generation_id IS NULL AND ? IS NULL) OR generation_id=?)
               AND status!='rejected'
               AND ((parent_id IS NULL AND ? IS NULL) OR parent_id=?)
               ORDER BY sort_order,node_id""",
            (
                first["course_id"], first["node_scope"], target["node_type"],
                first.get("material_type") or "other",
                first.get("generation_id"), first.get("generation_id"),
                target_parent_id, target_parent_id,
            ),
        )
        target_position = next(
            (index for index, row in enumerate(siblings) if row["node_id"] == target["node_id"]),
            -1,
        )
        if target_position < 0:
            raise ValidationError("目标节点已不在当前目录中，请刷新后重试")
        insert_at = target_position + (1 if position == "after" else 0)

        created_node_ids: list[str] = []
        top_wrappers: list[dict[str, Any]] = []
        wrapper_types = type_order[target_rank:moving_rank]
        with self.db.connect() as conn:
            for node in nodes:
                parent_id = target_parent_id
                top_wrapper: dict[str, Any] | None = None
                for wrapper_type in wrapper_types:
                    wrapper_id = f"kn_{uuid.uuid4().hex}"
                    fingerprint = f"manual-wrapper:{uuid.uuid4().hex}"
                    conn.execute(
                        """INSERT INTO knowledge_nodes(
                               node_id,course_id,document_id,node_scope,parent_id,node_type,title,
                               summary,markdown,keywords_json,source_pages_json,sort_order,status,
                               analysis_job_id,reviewed_by,reviewed_at,material_type,generation_id,
                               source_fingerprint
                           ) VALUES(?,?,?,?,?,?,?,'','','[]','[]',?,?,?,?,CURRENT_TIMESTAMP,?,?,?)""",
                        (
                            wrapper_id, node["course_id"], node.get("document_id"),
                            node["node_scope"], parent_id, wrapper_type, node["title"], 10,
                            node["status"], node.get("analysis_job_id"), actor["user_id"],
                            node.get("material_type") or "other", node.get("generation_id"),
                            fingerprint,
                        ),
                    )
                    created_node_ids.append(wrapper_id)
                    if top_wrapper is None:
                        top_wrapper = {"node_id": wrapper_id}
                    parent_id = wrapper_id
                conn.execute(
                    """UPDATE knowledge_nodes SET parent_id=?,sort_order=10,reviewed_by=?,
                       reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE node_id=?""",
                    (parent_id, actor["user_id"], node["node_id"]),
                )
                if top_wrapper:
                    top_wrappers.append(top_wrapper)

            ordered = [
                *siblings[:insert_at], *top_wrappers, *siblings[insert_at:],
            ]
            conn.executemany(
                """UPDATE knowledge_nodes SET sort_order=?,reviewed_by=?,
                   reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE node_id=?""",
                [
                    ((index + 1) * 10, actor["user_id"], row["node_id"])
                    for index, row in enumerate(ordered)
                ],
            )
        return {
            "moved_node_ids": unique,
            "created_node_ids": created_node_ids,
            "status": "promoted",
        }

    def restore_node_positions(
        self,
        actor: dict[str, Any],
        placements: list[dict[str, Any]],
        *,
        remove_node_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Restore a tree-position snapshot created immediately before a drag.

        The client sends all nodes in the active document/material partition so
        both the moved branch and any siblings renumbered by ``move_nodes`` are
        restored exactly. Every parent is revalidated server-side; this is not
        a way to bypass material, generation, or hierarchy isolation.
        """
        unique: dict[str, dict[str, Any]] = {}
        for placement in placements:
            node_id = str(placement.get("node_id") or "")
            if node_id:
                unique[node_id] = {
                    "node_id": node_id,
                    "parent_id": (
                        str(placement["parent_id"])
                        if placement.get("parent_id") else None
                    ),
                    "sort_order": max(0, int(placement.get("sort_order") or 0)),
                }
        if not unique:
            raise ValidationError("没有可撤销的目录位置")

        nodes = {node_id: self._require_node(actor, node_id) for node_id in unique}
        first = next(iter(nodes.values()))
        if any(
            node["course_id"] != first["course_id"]
            or node["node_scope"] != first["node_scope"]
            or node.get("material_type") != first.get("material_type")
            or node.get("generation_id") != first.get("generation_id")
            for node in nodes.values()
        ):
            raise ValidationError("只能撤销同一目录范围、同一材料分区的调整")

        expected_parent_types = {
            "chapter": None,
            "section": "chapter",
            "knowledge_point": "section",
        }
        parents: dict[str, dict[str, Any]] = {}
        for placement in unique.values():
            node = nodes[placement["node_id"]]
            node_type = str(node["node_type"])
            expected_parent_type = expected_parent_types.get(node_type)
            parent_id = placement["parent_id"]
            if expected_parent_type is None:
                if parent_id:
                    raise ValidationError("章节点只能恢复到知识树根目录")
                continue
            if not parent_id:
                raise ValidationError("节或知识点缺少原父目录，无法安全撤销")
            parent = parents.get(parent_id)
            if parent is None:
                parent = self._require_node(actor, parent_id)
                parents[parent_id] = parent
            if (
                parent["course_id"] != first["course_id"]
                or parent["node_scope"] != first["node_scope"]
                or parent["node_type"] != expected_parent_type
                or parent.get("material_type") != first.get("material_type")
                or parent.get("generation_id") != first.get("generation_id")
                or parent["status"] == "rejected"
            ):
                raise ValidationError("原目录层级已经变化，不能直接撤销")

        removable: list[dict[str, Any]] = []
        for node_id in dict.fromkeys(str(value) for value in (remove_node_ids or []) if str(value)):
            if node_id in unique:
                raise ValidationError("撤销快照不能同时恢复并删除同一节点")
            node = self._require_node(actor, node_id)
            if (
                node["course_id"] != first["course_id"]
                or node["node_scope"] != first["node_scope"]
                or node.get("material_type") != first.get("material_type")
                or node.get("generation_id") != first.get("generation_id")
                or not str(node.get("source_fingerprint") or "").startswith("manual-wrapper:")
            ):
                raise ValidationError("只能清理本次提升自动生成的空目录")
            removable.append(node)

        with self.db.connect() as conn:
            conn.executemany(
                """UPDATE knowledge_nodes SET parent_id=?,sort_order=?,reviewed_by=?,
                   reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE node_id=?""",
                [
                    (
                        placement["parent_id"], placement["sort_order"],
                        actor["user_id"], placement["node_id"],
                    )
                    for placement in unique.values()
                ],
            )
            rank = {"knowledge_point": 2, "section": 1, "chapter": 0}
            for node in sorted(removable, key=lambda value: rank.get(str(value["node_type"]), 0), reverse=True):
                child = conn.execute(
                    "SELECT 1 FROM knowledge_nodes WHERE parent_id=? LIMIT 1", (node["node_id"],)
                ).fetchone()
                if child:
                    raise ValidationError("自动目录已有其他内容，不能直接撤销")
                conn.execute("DELETE FROM knowledge_nodes WHERE node_id=?", (node["node_id"],))
        return {
            "restored": len(unique), "removed": len(removable), "status": "restored",
        }

    def merge_nodes(self, actor: dict[str, Any], node_ids: list[str], title: str) -> dict[str, Any]:
        unique = list(dict.fromkeys(node_ids))
        if len(unique) < 2:
            raise ValidationError("至少选择两个知识点进行合并")
        nodes = [self._require_node(actor, node_id) for node_id in unique]
        if any(node["node_type"] != "knowledge_point" or node["course_id"] != nodes[0]["course_id"] or
               node["node_scope"] != nodes[0]["node_scope"] or
               node.get("material_type") != nodes[0].get("material_type") or
               node.get("generation_id") != nodes[0].get("generation_id") for node in nodes):
            raise ValidationError("只能合并同一目录范围内的知识点")
        target = nodes[0]
        merged_fingerprint = self._outline_fingerprint(
            str(target.get("material_type") or "other"), "merged", [
                str(node.get("source_fingerprint") or node["node_id"]) for node in nodes
            ]
        )
        with self.db.connect() as conn:
            conn.execute(
                """UPDATE knowledge_nodes SET title=?,summary='',markdown=?,status='draft',
                   source_fingerprint=?,updated_at=CURRENT_TIMESTAMP
                   WHERE node_id=?""",
                (self._clean_title(title, target["title"]),
                 "\n\n".join(x["markdown"] for x in nodes if x["markdown"]),
                 merged_fingerprint, target["node_id"]),
            )
            for source in nodes[1:]:
                conn.execute(
                    """INSERT OR IGNORE INTO knowledge_node_sources(node_id,block_id,document_id,page_number,bbox_json)
                       SELECT ?,block_id,document_id,page_number,bbox_json FROM knowledge_node_sources WHERE node_id=?""",
                    (target["node_id"], source["node_id"]),
                )
                conn.execute("UPDATE knowledge_nodes SET status='rejected' WHERE node_id=?", (source["node_id"],))
            self._put_nodes_in_trash(
                conn, nodes[1:], str(actor["user_id"]),
                reason=f"已合并到知识点：{self._clean_title(title, target['title'])}",
                action_type="merged",
            )
        result = self.db.fetch_one("SELECT * FROM knowledge_nodes WHERE node_id=?", (target["node_id"],)) or {}
        result.pop("summary", None)
        return result

    def split_node(self, actor: dict[str, Any], node_id: str, parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        node = self._require_node(actor, node_id)
        if node["node_type"] != "knowledge_point" or len(parts) < 2:
            raise ValidationError("知识点拆分至少需要两个有效部分")
        created: list[dict[str, Any]] = []
        with self.db.connect() as conn:
            conn.execute("UPDATE knowledge_nodes SET status='rejected' WHERE node_id=?", (node_id,))
            self._put_nodes_in_trash(
                conn, [node], str(actor["user_id"]),
                reason="原知识点已拆分为多个知识点", action_type="split",
            )
            for offset, part in enumerate(parts, 1):
                new_id = f"kn_{uuid.uuid4().hex}"
                conn.execute(
                    """INSERT INTO knowledge_nodes(node_id,course_id,document_id,node_scope,parent_id,node_type,title,
                       summary,markdown,keywords_json,source_pages_json,sort_order,status,
                       material_type,generation_id,source_fingerprint)
                       VALUES(?,?,?,?,?,'knowledge_point',?,?,?,?,?,?,'draft',?,?,?)""",
                    (new_id, node["course_id"], node["document_id"], node["node_scope"], node["parent_id"],
                     self._clean_title(part.get("title"), f"{node['title']}（{offset}）"), "",
                     str(part.get("markdown") or ""), json.dumps(part.get("keywords") or [], ensure_ascii=False),
                     node["source_pages_json"], node["sort_order"] + offset,
                     node.get("material_type") or "other", node.get("generation_id"),
                     self._outline_fingerprint(
                         str(node.get("material_type") or "other"), "split", [
                             str(node.get("source_fingerprint") or node_id), str(offset),
                             str(part.get("title") or ""), str(part.get("markdown") or ""),
                         ],
                     )),
                )
                conn.execute(
                    """INSERT INTO knowledge_node_sources(node_id,block_id,document_id,page_number,bbox_json)
                       SELECT ?,block_id,document_id,page_number,bbox_json FROM knowledge_node_sources WHERE node_id=?""",
                    (new_id, node_id),
                )
                created.append({"node_id": new_id})
        results = [self.db.fetch_one("SELECT * FROM knowledge_nodes WHERE node_id=?", (x["node_id"],)) or {}
                   for x in created]
        for result in results:
            result.pop("summary", None)
        return results

    def list_trash(self, actor: dict[str, Any], course_id: str) -> list[dict[str, Any]]:
        course = self.campus.require_access(course_id, str(actor["user_id"]), "teacher")
        if course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权查看该课程回收站")
        rows = self.db.fetch_all(
            """SELECT n.node_id,n.document_id,n.node_scope,n.node_type,n.title,n.markdown,
                      n.parent_id,n.sort_order,t.trash_batch_id,t.original_parent_id,t.reason,
                      t.action_type,t.trashed_by,t.trashed_at,d.original_name
               FROM knowledge_node_trash t
               JOIN knowledge_nodes n USING(node_id)
               LEFT JOIN course_documents d ON d.document_id=n.document_id
               WHERE t.course_id=? AND n.status='rejected'
               ORDER BY t.trashed_at DESC,n.sort_order,n.node_id""",
            (course_id,),
        )
        for row in rows:
            row["sources"] = self.db.fetch_all(
                """SELECT s.document_id,s.page_number,d.original_name
                   FROM knowledge_node_sources s JOIN course_documents d USING(document_id)
                   WHERE s.node_id=? ORDER BY s.page_number LIMIT 5""",
                (row["node_id"],),
            )
        return rows

    def restore_trash_node(self, actor: dict[str, Any], node_id: str) -> dict[str, Any]:
        node = self._require_node(actor, node_id)
        trash = self.db.fetch_one(
            "SELECT * FROM knowledge_node_trash WHERE node_id=?", (node_id,)
        )
        if not trash or node["status"] != "rejected":
            raise ValidationError("该知识点不在回收站")
        with self.db.connect() as conn:
            rows = conn.execute(
                """WITH RECURSIVE descendants(node_id) AS (
                       SELECT node_id FROM knowledge_nodes WHERE node_id=?
                       UNION ALL
                       SELECT n.node_id FROM knowledge_nodes n JOIN descendants d ON n.parent_id=d.node_id
                   )
                   SELECT n.node_id FROM knowledge_nodes n
                   JOIN descendants d USING(node_id)
                   JOIN knowledge_node_trash t USING(node_id)
                   WHERE t.trash_batch_id=?""",
                (node_id, trash["trash_batch_id"]),
            ).fetchall()
            restore_ids = [row["node_id"] for row in rows] or [node_id]
            original_parent = trash.get("original_parent_id")
            if original_parent:
                parent = conn.execute(
                    "SELECT status FROM knowledge_nodes WHERE node_id=?", (original_parent,)
                ).fetchone()
                if not parent or parent["status"] == "rejected":
                    original_parent = None
            conn.execute(
                """UPDATE knowledge_nodes SET parent_id=?,status='draft',reviewed_by=?,
                   reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE node_id=?""",
                (original_parent, actor["user_id"], node_id),
            )
            conn.executemany(
                """UPDATE knowledge_nodes SET status='draft',reviewed_by=?,
                   reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE node_id=?""",
                [(actor["user_id"], value) for value in restore_ids if value != node_id],
            )
            placeholders = ",".join("?" for _ in restore_ids)
            conn.execute(
                f"""UPDATE document_blocks SET verification_status='review_required',
                    reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP
                    WHERE block_id IN (
                        SELECT block_id FROM knowledge_node_sources
                        WHERE node_id IN ({placeholders})
                    )""",
                (actor["user_id"], *restore_ids),
            )
            conn.execute(
                f"DELETE FROM knowledge_node_trash WHERE node_id IN ({placeholders})",
                tuple(restore_ids),
            )
        return {"node_id": node_id, "restored_node_ids": restore_ids, "status": "draft"}

    def permanently_delete_trash_node(self, actor: dict[str, Any], node_id: str) -> dict[str, Any]:
        node = self._require_node(actor, node_id)
        trash = self.db.fetch_one(
            "SELECT * FROM knowledge_node_trash WHERE node_id=?", (node_id,)
        )
        if not trash or node["status"] != "rejected":
            raise ValidationError("该知识点不在回收站")
        with self.db.connect() as conn:
            rows = conn.execute(
                """WITH RECURSIVE descendants(node_id) AS (
                       SELECT node_id FROM knowledge_nodes WHERE node_id=?
                       UNION ALL
                       SELECT n.node_id FROM knowledge_nodes n JOIN descendants d ON n.parent_id=d.node_id
                   )
                   SELECT n.node_id FROM knowledge_nodes n JOIN descendants d USING(node_id)
                   WHERE n.status='rejected'""",
                (node_id,),
            ).fetchall()
            delete_ids = [row["node_id"] for row in rows]
            placeholders = ",".join("?" for _ in delete_ids)
            published = conn.execute(
                f"""SELECT 1 FROM knowledge_version_nodes
                    WHERE node_id IN ({placeholders}) LIMIT 1""",
                tuple(delete_ids),
            ).fetchone()
            if published:
                raise ValidationError("该知识点被历史发布版本引用，只能保留在回收站")
            conn.execute(
                f"DELETE FROM knowledge_nodes WHERE node_id IN ({placeholders})",
                tuple(delete_ids),
            )
        return {"node_id": node_id, "deleted_node_ids": delete_ids, "deleted": True}

    def permanently_delete_trash_nodes(self, actor: dict[str, Any],
                                       node_ids: list[str]) -> dict[str, Any]:
        unique = list(dict.fromkeys(str(value) for value in node_ids if str(value)))
        deleted: list[str] = []
        failed: list[dict[str, str]] = []
        for node_id in unique:
            try:
                result = self.permanently_delete_trash_node(actor, node_id)
                deleted.extend(result["deleted_node_ids"])
            except (PermissionDenied, ValidationError, NotFound) as exc:
                # A parent deletion may already have removed a selected descendant.
                if node_id in deleted:
                    continue
                failed.append({"id": node_id, "message": str(exc)})
        return {
            "requested": len(unique),
            "deleted": list(dict.fromkeys(deleted)),
            "failed": failed,
        }

    def list_relations(self, actor: dict[str, Any], course_id: str, *,
                       material_type: str | None = None) -> list[dict[str, Any]]:
        course = self.campus.require_access(course_id, str(actor["user_id"]), "teacher")
        if course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权查看知识关系")
        if material_type is not None and material_type not in MATERIAL_TYPES:
            raise ValidationError("资料用途类型无效")
        condition = ""
        params: tuple[Any, ...] = (course_id, course_id, course_id)
        if material_type:
            condition = " AND r.material_type=?"
            params += (material_type,)
        return self.db.fetch_all(
            """SELECT r.*,s.title source_title,t.title target_title FROM knowledge_relations r
               JOIN knowledge_nodes s ON s.node_id=r.source_node_id JOIN knowledge_nodes t ON t.node_id=r.target_node_id
               WHERE r.course_id=? AND r.status!='rejected' AND s.status!='rejected' AND t.status!='rejected'
                 AND (r.generation_id IN (SELECT generation_id FROM course_outline_generations
                                         WHERE course_id=? AND status='current')
                      OR (r.generation_id IS NULL AND NOT EXISTS (
                          SELECT 1 FROM course_outline_generations WHERE course_id=? AND status='current'
                      )))"""
            + condition + " ORDER BY r.created_at", params,
        )

    def review_relation(self, actor: dict[str, Any], course_id: str, relation_id: str,
                        status: str) -> dict[str, Any]:
        self.list_relations(actor, course_id)
        if status not in {"draft", "approved", "rejected"}:
            raise ValidationError("关系审核状态无效")
        self.db.execute(
            """UPDATE knowledge_relations SET status=?,reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,
               updated_at=CURRENT_TIMESTAMP WHERE relation_id=? AND course_id=?""",
            (status, actor["user_id"], relation_id, course_id),
        )
        row = self.db.fetch_one("SELECT * FROM knowledge_relations WHERE relation_id=?", (relation_id,))
        if not row:
            raise NotFound("知识关系不存在")
        return row

    def publish_readiness(self, actor: dict[str, Any], course_id: str) -> dict[str, Any]:
        course = self.campus.require_access(course_id, str(actor["user_id"]), "teacher")
        if course["course_type"] != "shared_course" or course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("只能检查自己的共享课程")
        self._ensure_partitioned_course_outline(course_id)
        nodes = self.db.fetch_all(
            """SELECT * FROM knowledge_nodes WHERE course_id=? AND node_scope='course'
               AND content_domain='knowledge'
               AND (generation_id IN (SELECT generation_id FROM course_outline_generations
                                      WHERE course_id=? AND status='current')
                    OR (generation_id IS NULL AND NOT EXISTS (
                        SELECT 1 FROM course_outline_generations WHERE course_id=? AND status='current'
                    )))""",
            (course_id, course_id, course_id),
        )
        legacy_verified_blocks = int((self.db.fetch_one(
            """SELECT COUNT(*) n FROM document_blocks b JOIN course_documents d USING(document_id)
               WHERE d.course_id=? AND b.content_destination='knowledge'
                 AND b.verification_status IN ('auto_verified','teacher_verified')""",
            (course_id,),
        ) or {"n": 0})["n"])
        legacy_pending_blocks = int((self.db.fetch_one(
            """SELECT COUNT(*) n FROM document_blocks b JOIN course_documents d USING(document_id)
               WHERE d.course_id=? AND b.content_destination='knowledge'
                 AND b.verification_status='review_required'""",
            (course_id,),
        ) or {"n": 0})["n"])
        # Existing courses may have completed the original block-level teacher review
        # before a semantic tree was generated. Those verified source blocks remain a
        # valid, evidence-backed publication path while the v3 analysis runs separately.
        legacy_block_mode = not nodes and legacy_verified_blocks > 0
        counts = {status: sum(1 for node in nodes if node["status"] == status)
                  for status in ("draft", "approved", "rejected")}
        approved_points = [node for node in nodes
                           if node["node_type"] == "knowledge_point" and node["status"] == "approved"]
        source_less = [node for node in approved_points if not self.db.fetch_one(
            "SELECT 1 ok FROM knowledge_node_sources WHERE node_id=? LIMIT 1", (node["node_id"],)
        )]
        node_map = {node["node_id"]: node for node in nodes}
        rejected_ancestors: list[str] = []
        for node in approved_points:
            parent_id = node.get("parent_id")
            while parent_id and parent_id in node_map:
                parent = node_map[parent_id]
                if parent["status"] == "rejected":
                    rejected_ancestors.append(node["node_id"])
                    break
                parent_id = parent.get("parent_id")
        unclassified = int((self.db.fetch_one(
            """SELECT COUNT(*) n FROM document_blocks b JOIN course_documents d USING(document_id)
               WHERE d.course_id=? AND b.content_destination='unclassified'""", (course_id,),
        ) or {"n": 0})["n"])
        active_jobs = int((self.db.fetch_one(
            """SELECT COUNT(*) n FROM semantic_analysis_jobs WHERE course_id=?
               AND status IN ('queued','running','retry_wait')""", (course_id,),
        ) or {"n": 0})["n"])
        failed_jobs = int((self.db.fetch_one(
            """SELECT COUNT(*) n FROM (
                   SELECT document_id,MAX(created_at) created_at FROM semantic_analysis_jobs
                   WHERE course_id=? GROUP BY document_id
               ) latest JOIN semantic_analysis_jobs s USING(document_id,created_at)
               WHERE s.status='failed'""", (course_id,),
        ) or {"n": 0})["n"])
        syllabus_fallback_documents: set[str] = set()
        for row in self.db.fetch_all(
            """SELECT d.document_id,s.result_json
               FROM course_documents d
               JOIN document_material_metadata m ON m.document_id=d.document_id
               JOIN semantic_analysis_jobs s ON s.document_id=d.document_id
               WHERE d.course_id=? AND m.material_type='syllabus'
                 AND s.rowid=(SELECT latest.rowid FROM semantic_analysis_jobs latest
                              WHERE latest.document_id=d.document_id
                              ORDER BY latest.created_at DESC,latest.rowid DESC LIMIT 1)""",
            (course_id,),
        ):
            try:
                result = json.loads(row.get("result_json") or "{}")
            except json.JSONDecodeError:
                result = {}
            if (
                result.get("fallback_batches")
                or result.get("document_reduce_fallback")
                or result.get("course_reduce_fallback")
            ):
                syllabus_fallback_documents.add(str(row["document_id"]))
        syllabus_fallback_generations = int((self.db.fetch_one(
            """SELECT COUNT(*) n FROM course_outline_generations
               WHERE course_id=? AND material_type='syllabus' AND status='current'
                 AND TRIM(COALESCE(fallback_reason,''))!=''""",
            (course_id,),
        ) or {"n": 0})["n"])
        syllabus_fallback_count = max(
            len(syllabus_fallback_documents), syllabus_fallback_generations,
        )
        blockers: list[dict[str, Any]] = []
        for code, count, message, target in (
            ("no_approved_points", int(not approved_points and not legacy_block_mode), "没有已批准知识点", "/knowledge"),
            ("active_analysis", 0 if legacy_block_mode else active_jobs, "仍有语义分析任务运行中", "/knowledge"),
            ("failed_analysis", 0 if legacy_block_mode else failed_jobs, "存在尚未处理的分析失败", "/knowledge"),
            ("unclassified_blocks", unclassified, "仍有原文块等待人工分类", "/knowledge"),
            ("pending_verified_blocks", legacy_pending_blocks if legacy_block_mode else 0,
             "仍有知识原文块等待教师审核", "/knowledge"),
            ("missing_sources", len(source_less), "已批准知识点缺少原文来源", "/knowledge"),
            ("rejected_ancestor", len(rejected_ancestors), "已批准知识点位于已驳回目录下", "/knowledge"),
            ("syllabus_safe_fallback", syllabus_fallback_count,
             "教学大纲存在安全降级结果，必须重新完成无降级分析后才能发布", "/knowledge"),
        ):
            if count:
                blockers.append({"code": code, "count": count, "message": message, "target": target})
        version = self.db.fetch_one(
            """SELECT version_number,status,published_at FROM knowledge_versions WHERE course_id=?
               ORDER BY version_number DESC LIMIT 1""", (course_id,),
        ) or {"version_number": 0, "status": "unpublished", "published_at": None}
        document_count = int((self.db.fetch_one(
            "SELECT COUNT(*) n FROM course_documents WHERE course_id=?", (course_id,),
        ) or {"n": 0})["n"])
        return {
            "course_id": course_id, "can_publish": not blockers, "publication": version,
            "document_count": document_count, "node_counts": counts,
            "approved_knowledge_points": len(approved_points),
            "publication_mode": "verified_blocks" if legacy_block_mode else "knowledge_tree",
            "legacy_verified_blocks": legacy_verified_blocks,
            "unclassified_blocks": unclassified, "active_analysis_jobs": active_jobs,
            "failed_analysis_jobs": failed_jobs, "blockers": blockers,
            "syllabus_fallback_documents": len(syllabus_fallback_documents),
        }

    def teaching_overview(self, actor: dict[str, Any], course_id: str, *,
                          class_id: str | None = None) -> dict[str, Any]:
        course = self.campus.require_access(course_id, str(actor["user_id"]), "teacher")
        if course["course_type"] != "shared_course" or course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("教师只能查看自己的共享课程诊断")
        class_scope = None
        if class_id:
            class_scope = self.db.fetch_one(
                """SELECT cl.class_id,cl.class_name,t.term_name,
                          (SELECT COUNT(*) FROM class_memberships m
                           WHERE m.class_id=cl.class_id AND m.status='active') member_count
                   FROM classes cl JOIN terms t ON t.term_id=cl.term_id
                   WHERE cl.class_id=? AND cl.course_id=? AND cl.teacher_id=?""",
                (class_id, course_id, actor["user_id"]),
            )
            if not class_scope:
                raise PermissionDenied("无权查看该教学班")
        analysis = self.campus.class_analysis(course_id, str(actor["user_id"]))
        readiness = self.publish_readiness(actor, course_id)
        health = self.course_health(actor, course_id)
        people = self.db.fetch_one(
            """SELECT COUNT(DISTINCT user_id) active_students FROM (
                   SELECT user_id FROM course_questions WHERE course_id=?
                   UNION ALL SELECT user_id FROM course_attempts WHERE course_id=?
               )""", (course_id, course_id),
        ) or {"active_students": 0}
        attempts = self.db.fetch_all("SELECT score,total FROM course_attempts WHERE course_id=?", (course_id,))
        buckets = {"0-59": 0, "60-69": 0, "70-79": 0, "80-89": 0, "90-100": 0}
        for attempt in attempts:
            score = 100 * float(attempt["score"]) / max(1.0, float(attempt["total"]))
            key = "0-59" if score < 60 else "60-69" if score < 70 else "70-79" if score < 80 else "80-89" if score < 90 else "90-100"
            buckets[key] += 1
        priorities: list[dict[str, Any]] = []
        for blocker in readiness["blockers"]:
            priorities.append({"severity": "high", "type": blocker["code"], "title": blocker["message"],
                               "evidence_count": blocker["count"], "target": blocker["target"]})
        for row in analysis["uncovered_questions"][:5]:
            priorities.append({"severity": "high", "type": "uncovered_question", "title": row["question"],
                               "evidence_count": row["count"], "target": "/knowledge"})
        for row in analysis["weak_points"][:5]:
            priorities.append({"severity": "medium", "type": "weak_point", "title": row["knowledge_point"],
                               "evidence_count": row["answered"], "accuracy": row["accuracy"],
                               "target": "/questions"})
        return {
            "course": {"course_id": course_id, "course_name": course["course_name"]},
            "requested_class": class_scope,
            "data_scope": "course_only" if class_id else "course",
            "scope_note": ("历史学习事件没有 class_id，已安全回退为课程匿名聚合，未猜测班级归属"
                           if class_id else "共享课程匿名聚合"),
            "knowledge": {"readiness": readiness, "health": health},
            "learning": {**analysis, "active_students": int(people["active_students"]),
                         "score_buckets": buckets},
            "priorities": priorities,
        }

    def publish(self, actor: dict[str, Any], course_id: str) -> dict[str, Any]:
        course = self.campus.require_access(course_id, str(actor["user_id"]), "teacher")
        if course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权发布该课程知识库")
        readiness = self.publish_readiness(actor, course_id)
        if not readiness["can_publish"]:
            messages = "；".join(item["message"] for item in readiness["blockers"])
            raise ValidationError(f"知识库尚未达到发布条件：{messages}")
        outline_nodes = self.db.fetch_all(
            """SELECT * FROM knowledge_nodes WHERE course_id=? AND node_scope='course'
               AND (generation_id IN (SELECT generation_id FROM course_outline_generations
                                      WHERE course_id=? AND status='current')
                    OR (generation_id IS NULL AND NOT EXISTS (
                        SELECT 1 FROM course_outline_generations WHERE course_id=? AND status='current'
                    )))
               ORDER BY CASE material_type
                   WHEN 'slides' THEN 1 WHEN 'textbook' THEN 2 WHEN 'syllabus' THEN 3
                   WHEN 'lesson_plan' THEN 4 WHEN 'experiment' THEN 5 WHEN 'question_bank' THEN 6
                   WHEN 'knowledge_graph' THEN 7 WHEN 'teaching_schedule' THEN 8 ELSE 9 END,
                   sort_order""", (course_id, course_id, course_id),
        )
        approved_ids = {node["node_id"] for node in outline_nodes if node["status"] == "approved"}
        if approved_ids:
            node_map = {node["node_id"]: node for node in outline_nodes}
            node_ids = set(approved_ids)
            for node_id in list(approved_ids):
                parent_id = node_map[node_id].get("parent_id")
                while parent_id and parent_id in node_map:
                    node_ids.add(parent_id)
                    parent_id = node_map[parent_id].get("parent_id")
            markdown_lines: list[str] = []
            heading_marks = {"chapter": "##", "section": "###", "knowledge_point": "####"}
            active_material = ""
            for node in outline_nodes:
                if node["node_id"] not in node_ids:
                    continue
                if node["material_type"] != active_material:
                    active_material = str(node["material_type"])
                    markdown_lines.append(f"\n# {MATERIAL_LABELS.get(active_material, '其他')}")
                markdown_lines.append(f"\n{heading_marks[node['node_type']]} {node['title']}")
                if node["node_id"] not in approved_ids:
                    continue
                sources = self.db.fetch_all(
                    """SELECT DISTINCT d.original_name,s.page_number FROM knowledge_node_sources s
                       JOIN course_documents d USING(document_id) WHERE s.node_id=? ORDER BY s.page_number""",
                    (node["node_id"],),
                )
                source_text = "；".join(f"{x['original_name']} 第{x['page_number']}页" for x in sources)
                markdown_lines.extend([
                    node["markdown"],
                    f"来源：{source_text}" if source_text else "",
                ])
            approved_list = sorted(approved_ids)
            blocks = self.db.fetch_all(
                f"""SELECT DISTINCT block_id FROM knowledge_node_sources
                     WHERE node_id IN ({','.join('?' for _ in approved_list)})""",
                tuple(approved_list),
            )
            relations = self.db.fetch_all(
                """SELECT relation_id FROM knowledge_relations WHERE course_id=? AND status='approved'
                   AND (generation_id IN (SELECT generation_id FROM course_outline_generations
                                          WHERE course_id=? AND status='current')
                        OR (generation_id IS NULL AND NOT EXISTS (
                            SELECT 1 FROM course_outline_generations WHERE course_id=? AND status='current'
                        )))""",
                (course_id, course_id, course_id),
            )
            current = self.db.fetch_one("SELECT COALESCE(MAX(version_number),0) n FROM knowledge_versions WHERE course_id=?", (course_id,))
            version_id = f"kv_{uuid.uuid4().hex}"
            with self.db.connect() as conn:
                conn.execute("UPDATE knowledge_versions SET status='superseded' WHERE course_id=? AND status='published'", (course_id,))
                conn.execute(
                    """INSERT INTO knowledge_versions(version_id,course_id,version_number,status,created_by,published_at,markdown_snapshot)
                       VALUES(?,?,?,'published',?,CURRENT_TIMESTAMP,?)""",
                    (version_id, course_id, int(current["n"]) + 1, actor["user_id"], "\n".join(x for x in markdown_lines if x)),
                )
                conn.executemany("INSERT INTO knowledge_version_nodes(version_id,node_id) VALUES(?,?)",
                                 [(version_id, node_id) for node_id in node_ids])
                conn.executemany("INSERT INTO knowledge_version_blocks(version_id,block_id) VALUES(?,?)",
                                 [(version_id, row["block_id"]) for row in blocks])
                conn.executemany("INSERT INTO knowledge_version_relations(version_id,relation_id) VALUES(?,?)",
                                 [(version_id, row["relation_id"]) for row in relations])
            return self.db.fetch_one("SELECT * FROM knowledge_versions WHERE version_id=?", (version_id,)) or {}

        pending = self.db.fetch_one(
            """SELECT COUNT(*) n FROM document_blocks b JOIN course_documents d USING(document_id)
               WHERE d.course_id=? AND (b.content_destination='unclassified' OR
                    (b.content_destination='knowledge' AND b.verification_status='review_required'))""", (course_id,),
        )
        if pending and pending["n"]:
            raise ValidationError("仍有待审核知识块，不能发布")
        blocks = self.db.fetch_all(
            """SELECT b.block_id FROM document_blocks b JOIN course_documents d USING(document_id)
               WHERE d.course_id=? AND b.content_destination='knowledge'
                 AND b.verification_status IN ('auto_verified','teacher_verified')""", (course_id,),
        )
        if not blocks:
            raise ValidationError("没有可发布的知识块")
        current = self.db.fetch_one("SELECT COALESCE(MAX(version_number),0) n FROM knowledge_versions WHERE course_id=?", (course_id,))
        version_id = f"kv_{uuid.uuid4().hex}"
        with self.db.connect() as conn:
            conn.execute("UPDATE knowledge_versions SET status='superseded' WHERE course_id=? AND status='published'", (course_id,))
            conn.execute(
                """INSERT INTO knowledge_versions(version_id,course_id,version_number,status,created_by,published_at)
                   VALUES(?,?,?,'published',?,CURRENT_TIMESTAMP)""",
                (version_id, course_id, int(current["n"]) + 1, actor["user_id"]),
            )
            conn.executemany("INSERT INTO knowledge_version_blocks(version_id,block_id) VALUES(?,?)",
                             [(version_id, row["block_id"]) for row in blocks])
        return self.db.fetch_one("SELECT * FROM knowledge_versions WHERE version_id=?", (version_id,)) or {}
