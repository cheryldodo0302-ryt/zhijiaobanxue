from __future__ import annotations

import hashlib
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
from config import get_knowledge_extractor_settings
from document_ir import formula_anomalies, mineru_to_blocks, normalize_latex, search_aliases
from formula_client import Pix2TextClient
from job_secret_store import decrypt_job_secret, encrypt_job_secret
from llm_provider import GeminiProvider, QwenProvider
from mineru_client import MinerUClient
from semantic_knowledge_service import SemanticKnowledgeService


MATERIAL_TYPES = {
    "syllabus", "lesson_plan", "slides", "textbook", "experiment",
    "question_bank", "knowledge_graph", "teaching_schedule", "other",
}


class IngestionService:
    """Persistent orchestration boundary for native and external parsers."""

    def __init__(self, db: LearningDatabase, campus: CampusService):
        self.db = db
        self.campus = campus
        self.mineru = MinerUClient()
        self.formula = Pix2TextClient()
        self.semantic = SemanticKnowledgeService(campus.provider_factory)

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
        if source.suffix.lower() not in {".pptx", ".docx"}:
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
                       ai_settings: dict[str, str] | None = None) -> dict[str, Any]:
        return self.queue_document_stream(
            actor, course_id, name, mime_type, io.BytesIO(data),
            analysis_mode=analysis_mode, ai_settings=ai_settings,
        )

    def queue_document_stream(self, actor: dict[str, Any], course_id: str, name: str,
                              mime_type: str, stream: BinaryIO, *,
                              analysis_mode: str = "api",
                              ai_settings: dict[str, str] | None = None) -> dict[str, Any]:
        if actor.get("role") != "teacher":
            raise PermissionDenied("仅教师可以建设共享知识库")
        teacher_id = str(actor["user_id"])
        course = self.campus.require_access(course_id, teacher_id, "teacher")
        if course["course_type"] != "shared_course" or course["owner_id"] != teacher_id:
            raise PermissionDenied("只能向自己的共享课程上传资料")
        if analysis_mode not in {"api", "local"}:
            raise ValidationError("资料分析方式必须是 api 或 local")
        settings = ai_settings or {}
        custom_key = str(settings.get("api_key") or "").strip()
        custom_base = str(settings.get("base_url") or "").strip()
        custom_model = str(settings.get("model") or "").strip()
        custom_provider = str(settings.get("provider") or "openai_compatible").strip()
        if custom_key and (not custom_base or not custom_model):
            raise ValidationError("使用教师自有 API 时必须填写 Base URL 和模型名称")
        safe_name = _safe_name(name)
        suffix = Path(safe_name).suffix.lower()
        if suffix not in ALLOWED_FILES or mime_type not in ALLOWED_FILES[suffix]:
            raise ValidationError("扩展名与 MIME 类型不匹配或不受支持")
        document_id = f"doc_{uuid.uuid4().hex}"
        job_id = f"job_{uuid.uuid4().hex}"
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
            digest = digest_builder.hexdigest()
            if self.db.fetch_one(
                "SELECT 1 ok FROM course_documents WHERE course_id=? AND sha256=?", (course_id, digest)
            ):
                raise ValidationError("该课程中已存在内容相同的文件")
            staging.replace(destination)
            with self.db.connect() as conn:
                conn.execute(
                    """INSERT INTO course_documents(document_id,course_id,uploader_id,original_name,stored_path,mime_type,size_bytes,sha256,status)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (document_id, course_id, teacher_id, safe_name, str(destination), mime_type, size_bytes, digest, "queued"),
                )
                conn.execute(
                    """INSERT INTO ingestion_jobs(job_id,document_id,course_id,requested_by,status,
                           parser_config_hash,analysis_mode,ai_provider,ai_base_url,ai_model,ai_key_encrypted)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (job_id, document_id, course_id, teacher_id, "queued",
                     hashlib.sha256(
                         b"teacher-pdf-mineru-auto+native-office+canonical-markdown+pix2text-v3"
                     ).hexdigest(), analysis_mode, custom_provider if custom_key else "",
                     custom_base if custom_key else "", custom_model if custom_key else "",
                     encrypt_job_secret(custom_key)),
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
            chunks: list[dict[str, Any]] = []
            blocks: list[dict[str, Any]] = []
            parser_name = "zhijiao-native"
            parser_version = "1"
            canonical_markdown = ""
            if suffix == ".pdf":
                if not self.mineru.enabled:
                    raise ValidationError(
                        "PDF 必须通过 MinerU 解析；请配置远程 ZHIJIAO_MINERU_URL 后重试"
                    )
                middle = self.mineru.parse(
                    path, method="auto", asset_dir=path.parent / f"{job['document_id']}_assets"
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
            else:
                chunks = parse_document(path.read_bytes(), suffix)
            if chunks:
                previous_heading = ""
                for chunk in chunks:
                    content = chunk["content"]
                    heading = str(chunk.get("section") or "").strip()
                    heading_level = int(chunk.get("heading_level") or 0)
                    if heading_level and heading and heading != previous_heading:
                        blocks.append({
                            "block_type": "title", "markdown": f"{'#' * min(heading_level, 3)} {heading}",
                            "plain_text": heading, "latex": "",
                            "page_number": int(chunk.get("page_number") or 1), "bbox": [],
                            "confidence": None, "source_method": "native", "verification_status": "auto_verified",
                            "search_aliases": search_aliases(heading), "source_image_path": "", "raw": chunk,
                        })
                        previous_heading = heading
                    blocks.append({
                        "block_type": "paragraph", "markdown": content, "plain_text": content,
                        "latex": "", "page_number": int(chunk.get("page_number") or 1), "bbox": [],
                        "confidence": None, "source_method": "native", "verification_status": "review_required",
                        "search_aliases": search_aliases(content), "source_image_path": "", "raw": chunk,
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
                raise ValidationError(f"文件未解析出有效文字，已标记为需要 OCR{hint}")
            if not canonical_markdown:
                canonical_markdown = "\n\n".join(
                    str(block.get("markdown") or block.get("latex") or block.get("plain_text") or "").strip()
                    for block in blocks
                    if str(block.get("markdown") or block.get("latex") or block.get("plain_text") or "").strip()
                )
            self._write_canonical_markdown(job, canonical_markdown, parser_name, parser_version)
            self._ensure_material_metadata(
                job["document_id"], job["original_name"], canonical_markdown
            )
            self._create_office_preview(job)
            by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for block in blocks:
                block["block_id"] = f"block_{uuid.uuid4().hex}"
                by_page[int(block["page_number"])].append(block)
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
                for page_number, page_blocks in sorted(by_page.items()):
                    page_id = f"page_{uuid.uuid4().hex}"
                    page_pending = any(x["verification_status"] == "review_required" for x in page_blocks)
                    page_status = "review_required" if page_pending else "ready"
                    conn.execute(
                        """INSERT INTO document_pages(page_id,document_id,page_number,status,parse_method)
                           VALUES(?,?,?,?,?)""",
                        (page_id, job["document_id"], page_number, page_status, page_blocks[0]["source_method"]),
                    )
                    for block in page_blocks:
                        order += 1
                        if block["verification_status"] == "review_required":
                            pending_count += 1
                        conn.execute(
                            """INSERT INTO document_blocks(block_id,document_id,page_id,block_order,block_type,markdown,plain_text,
                               latex,source_image_path,page_number,bbox_json,confidence,source_method,verification_status,parser_name,parser_version,
                               search_aliases_json,raw_payload_json)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (block["block_id"], job["document_id"], page_id, order, block["block_type"],
                             block["markdown"], block["plain_text"], block["latex"], block.get("source_image_path", ""), page_number,
                             json.dumps(block["bbox"], ensure_ascii=False), block["confidence"], block["source_method"],
                             block["verification_status"], parser_name, parser_version,
                             json.dumps(block["search_aliases"], ensure_ascii=False),
                             json.dumps(block["raw"], ensure_ascii=False)),
                        )
                final_status = "review_required" if pending_count else "ready"
                conn.execute(
                    """UPDATE ingestion_jobs SET status=?,progress=100,total_pages=?,completed_pages=?,
                       updated_at=CURRENT_TIMESTAMP WHERE job_id=?""",
                    (final_status, len(by_page), len(by_page), job_id),
                )
                conn.execute("UPDATE course_documents SET status=?,error_message='' WHERE document_id=?",
                             (final_status, job["document_id"]))
            self.db.execute(
                """INSERT INTO semantic_analysis_jobs(
                       analysis_job_id,document_id,course_id,requested_by,status,current_stage,analysis_mode,
                       ai_provider,ai_base_url,ai_model,ai_key_encrypted
                   ) VALUES(?,?,?,?, 'queued','queued',?,?,?,?,?)""",
                (f"saj_{uuid.uuid4().hex}", job["document_id"], job["course_id"],
                 job["requested_by"], job.get("analysis_mode") or "api",
                 job.get("ai_provider") or "", job.get("ai_base_url") or "",
                 job.get("ai_model") or "", job.get("ai_key_encrypted") or ""),
            )
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
                                ai_settings: dict[str, str] | None = None) -> dict[str, Any]:
        document = self.require_document_access(actor, document_id)
        if actor.get("role") != "teacher":
            raise PermissionDenied("仅教师可以启动共享资料语义分析")
        if analysis_mode not in {"api", "local"}:
            raise ValidationError("资料分析方式必须是 api 或 local")
        settings = ai_settings or {}
        custom_key = str(settings.get("api_key") or "").strip()
        custom_base = str(settings.get("base_url") or "").strip()
        custom_model = str(settings.get("model") or "").strip()
        custom_provider = str(settings.get("provider") or "openai_compatible").strip()
        if custom_key and (not custom_base or not custom_model):
            raise ValidationError("使用教师自有 API 时必须填写 Base URL 和模型名称")
        active = self.db.fetch_one(
            """SELECT * FROM semantic_analysis_jobs WHERE document_id=?
               AND status IN ('queued','running','retry_wait') ORDER BY created_at DESC LIMIT 1""", (document_id,),
        )
        if active:
            active_uses_custom_api = bool(str(active.get("ai_key_encrypted") or ""))
            requested_uses_custom_api = bool(custom_key)
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
             custom_provider if custom_key else "", custom_base if custom_key else "",
             custom_model if custom_key else "", encrypt_job_secret(custom_key)),
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
                "UPDATE semantic_analysis_jobs SET status='failed',error_message='资料没有 DocumentIR 块' WHERE analysis_job_id=?",
                (analysis_job_id,),
            )
            return
        self._ensure_canonical_artifact(job["document_id"], blocks)
        course = self.db.fetch_one(
            "SELECT course_type FROM courses WHERE course_id=?", (job["course_id"],)
        ) or {}
        if course.get("course_type") != "shared_course":
            self.db.execute(
                "UPDATE semantic_analysis_jobs SET status='failed',current_stage='failed',error_message=? WHERE analysis_job_id=?",
                ("教师语义分析只允许 shared_course", analysis_job_id),
            )
            return
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
        if encrypted_key:
            api_key = decrypt_job_secret(encrypted_key)
            provider_name = str(job.get("ai_provider") or "openai_compatible").lower()
            base_url = str(job.get("ai_base_url") or "")
            model = str(job.get("ai_model") or "")
            provider = (
                GeminiProvider(api_key, base_url, model, timeout=115)
                if provider_name in {"gemini", "google", "google_gemini"}
                else QwenProvider(api_key, base_url, model, timeout=115)
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
            "遗漏候选", "证据不能",
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
                flush()
                current_title = re.sub(r"^#{1,6}\s*", "", content).strip()[:180]
            current_ids.append(str(block["block_id"]))
            current_text.append(content)
            if len(current_ids) >= 5 or sum(len(value) for value in current_text) >= 2400:
                flush()
        flush()
        return {"classifications": classifications, "knowledge_points": points}

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

    @staticmethod
    def _evidence_batches(blocks: list[dict[str, Any]], *, max_tokens: int = 3200,
                          max_blocks: int = 36) -> list[list[dict[str, Any]]]:
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

    def _process_evidence_tree_analysis(self, job: dict[str, Any],
                                        blocks: list[dict[str, Any]]) -> None:
        analysis_job_id = str(job["analysis_job_id"])
        semantic_blocks, skipped_classifications = self._prepare_semantic_blocks(blocks)
        batches = self._evidence_batches(semantic_blocks)
        planned_total = len(batches) + 2  # document reduce + course reduce
        try:
            checkpoint = json.loads(job.get("result_json") or "{}")
        except json.JSONDecodeError:
            checkpoint = {}
        if checkpoint.get("schema_version") != 4 or checkpoint.get("map_batch_count") != len(batches):
            checkpoint = {
                "schema_version": 4, "extractor": "evidence-map-reduce",
                "map_batch_count": len(batches), "map_results": [],
                "skipped_blocks": len(skipped_classifications), "fallback_batches": [],
            }
        map_results = list(checkpoint.get("map_results") or [])
        completed_maps = min(len(map_results), len(batches))
        self.db.execute(
            """UPDATE semantic_analysis_jobs SET status='running',current_stage='provider_preflight',
               current_batch=?,total_batches=?,error_message='',analyzer_version='evidence-map-reduce-v4',
               prompt_version='teacher-knowledge-v4',result_json=?,updated_at=CURRENT_TIMESTAMP
               WHERE analysis_job_id=?""",
            (completed_maps, planned_total, json.dumps(checkpoint, ensure_ascii=False), analysis_job_id),
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
                result = self.semantic.analyze_document_batch(
                    batches[batch_index], previous,
                    on_call=lambda: self._analysis_call(analysis_job_id),
                )
                used_fallback = False
            except Exception as exc:
                if not self._is_structured_output_error(exc):
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
        if not candidates:
            with self.db.connect() as conn:
                for item in classifications:
                    conn.execute(
                        """UPDATE document_blocks SET content_destination=?,semantic_role=?,
                           analysis_confidence=?,analysis_reason=?,verification_status='rejected',
                           updated_at=CURRENT_TIMESTAMP WHERE block_id=?""",
                        (item["destination"], item["semantic_role"], item["confidence"],
                         item["reason"], item["block_id"]),
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
            if not self._is_structured_output_error(exc):
                raise
            points = self._fallback_reduced_points(candidates)
            checkpoint["document_reduce_fallback"] = (
                "文档归并 JSON 被截断，保留分批原文结构供教师审核"
            )
        checkpoint["document_points"] = points
        self.db.execute(
            """UPDATE semantic_analysis_jobs SET current_batch=?,result_json=?,updated_at=CURRENT_TIMESTAMP
               WHERE analysis_job_id=?""",
            (len(batches) + 1, json.dumps(checkpoint, ensure_ascii=False), analysis_job_id),
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
                   verification_status='review_required' WHERE document_id=?""",
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

        self._rebuild_question_drafts(job["document_id"], job["course_id"])
        self.db.execute(
            "UPDATE semantic_analysis_jobs SET current_stage='course_reduce' WHERE analysis_job_id=?",
            (analysis_job_id,),
        )
        course_fallback = self._rebuild_course_outline(job["course_id"], analysis_job_id)
        if course_fallback:
            checkpoint["course_reduce_fallback"] = (
                "课程归并 JSON 被截断，保留文档知识点供教师审核"
            )
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

    def _build_faithful_document_outline(self, job: dict[str, Any], analysis_job_id: str) -> None:
        document = self.db.fetch_one(
            "SELECT original_name FROM course_documents WHERE document_id=?", (job["document_id"],)
        ) or {"original_name": "正文"}
        blocks = self.db.fetch_all(
            """SELECT * FROM document_blocks WHERE document_id=? AND content_destination='knowledge'
               ORDER BY page_number,block_order""", (job["document_id"],),
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

    def _rebuild_course_outline(self, course_id: str, analysis_job_id: str, *,
                                use_api: bool = True) -> bool:
        source_nodes = self.db.fetch_all(
            """SELECT n.*,s.title section_title,c.title chapter_title
               FROM knowledge_nodes n
               LEFT JOIN knowledge_nodes s ON s.node_id=n.parent_id
               LEFT JOIN knowledge_nodes c ON c.node_id=s.parent_id
               JOIN course_documents d ON d.document_id=n.document_id
               WHERE n.course_id=? AND n.node_scope='document' AND n.node_type='knowledge_point'
                 AND n.status!='rejected'
               ORDER BY d.created_at,n.sort_order""", (course_id,),
        )
        if not source_nodes:
            raise ValidationError("原文没有生成可审核的知识点")
        used_fallback = False
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
                    source_nodes, on_call=lambda: self._analysis_call(analysis_job_id)
                )
            except Exception as exc:
                if not self._is_structured_output_error(exc):
                    raise
                used_fallback = True
                unified = {
                    "points": [{
                        "course_key": f"safe-course-{index + 1}",
                        "chapter": row.get("chapter_title") or "待教师整理",
                        "section": row.get("section_title") or "原文安全降级",
                        "title": row["title"],
                        "keywords": json.loads(row.get("keywords_json") or "[]"),
                        "source_node_ids": [row["node_id"]],
                    } for index, row in enumerate(source_nodes)],
                    "relations": [],
                    "fallback_reason": "课程归并 JSON 被截断，保留原文知识点供教师审核",
                }
        suggestions = unified.get("points") if isinstance(unified, dict) else None
        relations = unified.get("relations") if isinstance(unified, dict) else None
        if not isinstance(suggestions, list) or not suggestions:
            raise ValidationError("AI 未生成课程统一目录")
        if not isinstance(relations, list):
            relations = []
        source_map = {row["node_id"]: row for row in source_nodes}
        with self.db.connect() as conn:
            conn.execute("DELETE FROM knowledge_relations WHERE course_id=? AND status!='approved'", (course_id,))
            conn.execute(
                """DELETE FROM knowledge_nodes WHERE course_id=? AND node_scope='course'
                   AND NOT EXISTS (SELECT 1 FROM knowledge_version_nodes vn WHERE vn.node_id=knowledge_nodes.node_id)""",
                (course_id,),
            )
            chapter_ids: dict[str, str] = {}
            section_ids: dict[tuple[str, str], str] = {}
            point_ids: dict[str, str] = {}
            order = 0
            for index, suggestion in enumerate(suggestions):
                if not isinstance(suggestion, dict):
                    continue
                source_ids = [str(value) for value in suggestion.get("source_node_ids", []) if str(value) in source_map]
                if not source_ids:
                    continue
                chapter = self._clean_title(suggestion.get("chapter"), "未分章")
                section = self._clean_title(suggestion.get("section"), "未分节")
                if chapter not in chapter_ids:
                    order += 1
                    chapter_ids[chapter] = f"kn_{uuid.uuid4().hex}"
                    conn.execute(
                        """INSERT INTO knowledge_nodes(node_id,course_id,node_scope,node_type,title,sort_order,analysis_job_id)
                           VALUES(?,?,'course','chapter',?,?,?)""",
                        (chapter_ids[chapter], course_id, chapter, order, analysis_job_id),
                    )
                section_key = (chapter, section)
                if section_key not in section_ids:
                    order += 1
                    section_ids[section_key] = f"kn_{uuid.uuid4().hex}"
                    conn.execute(
                        """INSERT INTO knowledge_nodes(node_id,course_id,node_scope,parent_id,node_type,title,sort_order,analysis_job_id)
                           VALUES(?,?,'course',?,'section',?,?,?)""",
                        (section_ids[section_key], course_id, chapter_ids[chapter], section, order, analysis_job_id),
                    )
                node_id = f"kn_{uuid.uuid4().hex}"
                course_key = str(suggestion.get("course_key") or f"course-point-{index + 1}")
                point_ids[course_key] = node_id
                source_rows = [source_map[source_id] for source_id in source_ids]
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
                order += 1
                conn.execute(
                    """INSERT INTO knowledge_nodes(node_id,course_id,node_scope,parent_id,node_type,title,summary,markdown,
                       keywords_json,source_pages_json,sort_order,analysis_job_id)
                       VALUES(?,?,'course',?,'knowledge_point',?,?,?,?,?,?,?)""",
                    (node_id, course_id, section_ids[section_key],
                     self._clean_title(suggestion.get("title"), "知识点"),
                     "", markdown,
                     json.dumps(suggestion.get("keywords") or [], ensure_ascii=False),
                     json.dumps(pages), order, analysis_job_id),
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
                conn.execute(
                    """INSERT OR IGNORE INTO knowledge_relations(relation_id,course_id,source_node_id,target_node_id,
                       relation_type,confidence,reason) VALUES(?,?,?,?,?,?,?)""",
                    (f"kr_{uuid.uuid4().hex}", course_id, source, target, relation_type, confidence,
                     str(relation.get("reason") or "")[:500]),
                )
        return used_fallback

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
        for key in ("document_reduce_fallback", "course_reduce_fallback", "notice"):
            if result.get(key):
                warnings.append(str(result[key]))
        row["warnings"] = warnings
        row["analysis_summary"] = {
            "skipped_blocks": skipped,
            "fallback_batches": len(fallback_batches),
            "used_document_fallback": bool(result.get("document_reduce_fallback")),
            "used_course_fallback": bool(result.get("course_reduce_fallback")),
        }
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
        reset_batch = checkpoint.get("schema_version") != 4 or not isinstance(
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
                       (SELECT s.api_calls FROM semantic_analysis_jobs s WHERE s.document_id=j.document_id ORDER BY s.created_at DESC LIMIT 1) analysis_api_calls
               FROM ingestion_jobs j JOIN course_documents d USING(document_id)
               LEFT JOIN document_material_metadata m USING(document_id)
               WHERE j.course_id=? ORDER BY j.created_at DESC""", (course_id,),
        )
        for row in rows:
            row["tags"] = json.loads(row.pop("tags_json") or "[]")
            row.pop("ai_key_encrypted", None)
        return rows

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
        return row

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
            return {
                "preview_kind": "pptx",
                "conversion_status": "ready",
                "preview_error": "",
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
        if job["status"] not in {"failed", "cancelled"}:
            raise ValidationError("只有失败或已取消的任务可以重试")
        self.db.execute(
            """UPDATE ingestion_jobs SET status='queued',progress=0,error_message='',failed_pages=0,
               updated_at=CURRENT_TIMESTAMP WHERE job_id=?""", (job_id,)
        )
        self.db.execute("UPDATE course_documents SET status='queued',error_message='' WHERE document_id=?",
                        (job["document_id"],))
        return self.get_job(actor, job_id)

    def course_health(self, actor: dict[str, Any], course_id: str) -> dict[str, Any]:
        course = self.campus.require_access(course_id, str(actor["user_id"]), "teacher")
        if course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权查看该课程体检单")
        rows = self.db.fetch_all(
            """SELECT p.status,p.parse_method,
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
            "local_processing_ratio": 1.0 if total else 0.0,
            "cloud_model_calls": 0, "cloud_tokens": 0,
            "publication": version or {"version_number": 0, "status": "unpublished", "published_at": None},
        }

    def list_blocks(self, actor: dict[str, Any], document_id: str) -> list[dict[str, Any]]:
        doc = self.db.fetch_one("SELECT * FROM course_documents WHERE document_id=?", (document_id,))
        if not doc:
            raise NotFound("资料不存在")
        course = self.campus.require_access(doc["course_id"], str(actor["user_id"]), "teacher")
        if course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权审核该资料")
        rows = self.db.fetch_all(
            "SELECT * FROM document_blocks WHERE document_id=? ORDER BY page_number,block_order", (document_id,)
        )
        for row in rows:
            row["bbox"] = json.loads(row.pop("bbox_json"))
            row["search_aliases"] = json.loads(row.pop("search_aliases_json"))
        return rows

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
        destination = "knowledge" if accepted else "excluded"
        self.db.execute(
            """UPDATE document_blocks SET markdown=?,plain_text=?,latex=?,visibility_level=?,verification_status=?,
               content_destination=?,reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,
               updated_at=CURRENT_TIMESTAMP WHERE block_id=?""",
            (markdown, plain_text, latex, visibility_level, status, destination, actor["user_id"], block_id),
        )
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
                 scope: str) -> dict[str, Any]:
        course = self.campus.require_access(course_id, str(actor["user_id"]), "teacher")
        if course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权查看该知识目录")
        condition = "course_id=? AND node_scope=?"
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
            latest = self.db.fetch_one(
                """SELECT analysis_job_id FROM semantic_analysis_jobs WHERE course_id=?
                   AND status IN ('review_required','completed') ORDER BY created_at DESC LIMIT 1""",
                (course_id,),
            )
        if latest:
            condition += " AND (analysis_job_id=? OR analysis_job_id IS NULL)"
            params += (latest["analysis_job_id"],)
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
        return {"nodes": rows, "relations": self.list_relations(actor, course_id)}

    def document_outline(self, actor: dict[str, Any], document_id: str) -> dict[str, Any]:
        document = self.require_document_access(actor, document_id)
        return self._outline(actor, document["course_id"], document_id=document_id, scope="document")

    def course_outline(self, actor: dict[str, Any], course_id: str) -> dict[str, Any]:
        return self._outline(actor, course_id, document_id=None, scope="course")

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
            if parent["course_id"] != node["course_id"] or parent["node_scope"] != node["node_scope"]:
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
            if status == "rejected" and node["node_type"] in {"chapter", "section"}:
                affected_rows = conn.execute(
                    """WITH RECURSIVE descendants(node_id) AS (
                           SELECT node_id FROM knowledge_nodes WHERE node_id=?
                           UNION ALL
                           SELECT n.node_id FROM knowledge_nodes n JOIN descendants d ON n.parent_id=d.node_id
                       ) SELECT n.* FROM knowledge_nodes n JOIN descendants d USING(node_id)""", (node_id,),
                ).fetchall()
                affected_ids = [row["node_id"] for row in affected_rows]
                conn.executemany(
                    """UPDATE knowledge_nodes SET status='rejected',reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,
                       updated_at=CURRENT_TIMESTAMP WHERE node_id=?""",
                    [(actor["user_id"], value) for value in affected_ids],
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
                conn.execute("DELETE FROM knowledge_node_trash WHERE node_id=?", (node_id,))
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
        updated.pop("summary", None)
        return updated

    def merge_nodes(self, actor: dict[str, Any], node_ids: list[str], title: str) -> dict[str, Any]:
        unique = list(dict.fromkeys(node_ids))
        if len(unique) < 2:
            raise ValidationError("至少选择两个知识点进行合并")
        nodes = [self._require_node(actor, node_id) for node_id in unique]
        if any(node["node_type"] != "knowledge_point" or node["course_id"] != nodes[0]["course_id"] or
               node["node_scope"] != nodes[0]["node_scope"] for node in nodes):
            raise ValidationError("只能合并同一目录范围内的知识点")
        target = nodes[0]
        with self.db.connect() as conn:
            conn.execute(
                """UPDATE knowledge_nodes SET title=?,summary='',markdown=?,status='draft',updated_at=CURRENT_TIMESTAMP
                   WHERE node_id=?""",
                (self._clean_title(title, target["title"]),
                 "\n\n".join(x["markdown"] for x in nodes if x["markdown"]), target["node_id"]),
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
                       summary,markdown,keywords_json,source_pages_json,sort_order,status)
                       VALUES(?,?,?,?,?,'knowledge_point',?,?,?,?,?,?,'draft')""",
                    (new_id, node["course_id"], node["document_id"], node["node_scope"], node["parent_id"],
                     self._clean_title(part.get("title"), f"{node['title']}（{offset}）"), "",
                     str(part.get("markdown") or ""), json.dumps(part.get("keywords") or [], ensure_ascii=False),
                     node["source_pages_json"], node["sort_order"] + offset),
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

    def list_relations(self, actor: dict[str, Any], course_id: str) -> list[dict[str, Any]]:
        course = self.campus.require_access(course_id, str(actor["user_id"]), "teacher")
        if course["owner_id"] != actor["user_id"]:
            raise PermissionDenied("无权查看知识关系")
        return self.db.fetch_all(
            """SELECT r.*,s.title source_title,t.title target_title FROM knowledge_relations r
               JOIN knowledge_nodes s ON s.node_id=r.source_node_id JOIN knowledge_nodes t ON t.node_id=r.target_node_id
               WHERE r.course_id=? AND r.status!='rejected' AND s.status!='rejected' AND t.status!='rejected'
               ORDER BY r.created_at""", (course_id,),
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
        latest = self.db.fetch_one(
            """SELECT analysis_job_id FROM semantic_analysis_jobs WHERE course_id=?
               AND status IN ('review_required','completed') ORDER BY created_at DESC LIMIT 1""",
            (course_id,),
        )
        params: tuple[Any, ...] = (course_id,)
        condition = "course_id=? AND node_scope='course'"
        if latest:
            condition += " AND (analysis_job_id=? OR analysis_job_id IS NULL)"
            params += (latest["analysis_job_id"],)
        nodes = self.db.fetch_all(f"SELECT * FROM knowledge_nodes WHERE {condition}", params)
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
               ORDER BY sort_order""", (course_id,),
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
            heading_marks = {"chapter": "#", "section": "##", "knowledge_point": "###"}
            for node in outline_nodes:
                if node["node_id"] not in node_ids:
                    continue
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
                "SELECT relation_id FROM knowledge_relations WHERE course_id=? AND status='approved'", (course_id,),
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
