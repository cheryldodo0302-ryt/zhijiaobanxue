from __future__ import annotations

import importlib.util
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Mapping

from campus_service import ValidationError
from config import get_knowledge_extractor_settings
from course_knowledge_template import CourseKnowledgeTree


def _parse_json(value: Any) -> dict[str, Any] | list[Any]:
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    starts = [index for index in (text.find("{"), text.find("[")) if index >= 0]
    if starts:
        text = text[min(starts):]
    try:
        result, _end = json.JSONDecoder(strict=False).raw_decode(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Docling Graph 的模型响应不是合法 JSON：{text[:240]}") from exc
    if not isinstance(result, (dict, list)):
        raise ValidationError("Docling Graph 的模型响应必须是 JSON 对象或数组")
    return result


class ExistingProviderJSONClient:
    """Expose the project's existing Qwen/relay provider as Docling Graph's LLM client."""

    model = "zhijiao-existing-provider"
    model_id = model
    streaming = False

    def __init__(
        self,
        provider_factory: Callable[[], Any],
        *,
        on_call: Callable[[], None] | None = None,
        context_limit: int = 128000,
        max_tokens: int = 8192,
    ):
        self.provider_factory = provider_factory
        self.on_call = on_call
        self.context_limit = context_limit
        self.max_tokens = max_tokens
        self._max_output_tokens = max_tokens
        self.last_call_diagnostics: dict[str, Any] = {}

    def get_json_response(
        self,
        prompt: str | Mapping[str, str],
        schema_json: str,
        structured_output: bool = True,
        response_top_level: Literal["object", "array"] = "object",
        response_schema_name: str = "extraction_result",
    ) -> dict[str, Any] | list[Any]:
        del structured_output, response_top_level, response_schema_name
        if self.on_call:
            self.on_call()
        provider = self.provider_factory()
        if provider is None:
            raise ValidationError("AI Provider 不可用，无法运行 Docling Graph")
        if isinstance(prompt, Mapping):
            system = str(prompt.get("system") or "")
            user = str(prompt.get("user") or "")
        else:
            system, user = "", str(prompt)
        if "=== TARGET SCHEMA ===" not in user and schema_json not in user:
            user = (
                f"{user}\n\n请严格返回符合以下 JSON Schema 的 JSON，不要输出 Markdown 代码围栏："
                f"\n{schema_json}"
            )
        generate_json = getattr(provider, "generate_json", None)
        raw = generate_json(system, user) if callable(generate_json) else provider.generate(system, user)
        result = _parse_json(raw)
        self.last_call_diagnostics = {
            "provider": type(provider).__name__,
            "model": self.model,
            "structured_attempted": False,
            "structured_failed": False,
            "fallback_used": True,
            "truncated": False,
        }
        return result

    def get_json_response_stream(self, *args: Any, **kwargs: Any) -> Iterator[dict[str, Any] | list[Any]]:
        yield self.get_json_response(*args, **kwargs)


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


class DoclingGraphKnowledgeAdapter:
    """Optional Docling Graph backend for teacher shared-course knowledge trees."""

    def __init__(
        self,
        provider_factory: Callable[[], Any],
        settings: dict[str, Any] | None = None,
        runner: Callable[[dict[str, Any]], Any] | None = None,
    ):
        self.provider_factory = provider_factory
        self.settings = settings or get_knowledge_extractor_settings()
        self._runner = runner

    @property
    def enabled(self) -> bool:
        return self.settings.get("backend") == "docling_graph"

    @staticmethod
    def installed() -> bool:
        return importlib.util.find_spec("docling_graph") is not None

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.settings.get("backend", "builtin"),
            "enabled": self.enabled,
            "installed": self.installed(),
            "contract": self.settings.get("contract", "auto"),
            "uses_existing_ai_provider": True,
        }

    @staticmethod
    def _annotated_markdown(
        blocks: list[dict[str, Any]], document_title: str, canonical_markdown: str = ""
    ) -> str:
        header = [
            f"[ZHIJIAO_DOCUMENT_TITLE:{document_title}]",
            "下文是完整 canonical Markdown。每个 ZHIJIAO_BLOCK 标记都属于原始资料，"
            "抽取结果必须逐字复制这些 block_id。",
        ]
        canonical = canonical_markdown.strip()
        if canonical:
            insertions: list[tuple[int, str]] = []
            unmatched: list[dict[str, Any]] = []
            cursor = 0
            for block in blocks:
                text = str(block.get("markdown") or block.get("latex") or block.get("plain_text") or "").strip()
                if not text:
                    continue
                position = canonical.find(text, cursor)
                if position < 0:
                    unmatched.append(block)
                    continue
                marker = (
                    f"[ZHIJIAO_BLOCK:{block['block_id']}|PAGE:{int(block.get('page_number') or 1)}]\n"
                )
                insertions.append((position, marker))
                cursor = position + len(text)
            for position, marker in reversed(insertions):
                canonical = canonical[:position] + marker + canonical[position:]
            parts = header + [canonical]
            if unmatched:
                parts.append("## 未能在 canonical Markdown 中逐字对齐的来源块索引")
                for block in unmatched:
                    text = str(block.get("markdown") or block.get("latex") or block.get("plain_text") or "").strip()
                    parts.append(
                        f"[ZHIJIAO_BLOCK:{block['block_id']}|PAGE:{int(block.get('page_number') or 1)}]\n{text}"
                    )
            return "\n\n".join(parts) + "\n"
        parts = header
        for block in blocks:
            text = str(block.get("markdown") or block.get("latex") or block.get("plain_text") or "").strip()
            if not text:
                continue
            parts.append(
                f"[ZHIJIAO_BLOCK:{block['block_id']}|PAGE:{int(block.get('page_number') or 1)}]\n{text}"
            )
        return "\n\n".join(parts) + "\n"

    def _load_runner(self) -> Callable[[dict[str, Any]], Any]:
        if self._runner is not None:
            return self._runner
        if not self.installed():
            raise ValidationError(
                "已配置 Docling Graph，但服务器尚未安装。请运行："
                "python -m pip install -r requirements-docling-graph.txt"
            )
        from docling_graph import run_pipeline

        return run_pipeline

    def analyze(
        self,
        blocks: list[dict[str, Any]],
        *,
        document_title: str,
        canonical_markdown: str = "",
        on_call: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise ValidationError("Docling Graph 知识树后端未启用")
        block_map = {
            str(block["block_id"]): block
            for block in blocks
            if str(block.get("markdown") or block.get("latex") or block.get("plain_text") or "").strip()
        }
        if not block_map:
            raise ValidationError("资料没有可供 Docling Graph 分析的 Markdown 块")
        markdown = self._annotated_markdown(
            list(block_map.values()), document_title, canonical_markdown=canonical_markdown
        )
        client = ExistingProviderJSONClient(
            self.provider_factory,
            on_call=on_call,
            context_limit=int(self.settings.get("context_limit") or 128000),
            max_tokens=int(self.settings.get("max_output_tokens") or 8192),
        )
        runner = self._load_runner()
        with tempfile.TemporaryDirectory(prefix="zhijiao-docling-graph-") as temp_dir:
            source = Path(temp_dir) / "canonical_with_block_markers.md"
            source.write_text(markdown, encoding="utf-8")
            context = runner({
                "source": str(source),
                "template": CourseKnowledgeTree,
                "backend": "llm",
                "inference": "remote",
                "processing_mode": "many-to-one",
                "extraction_contract": self.settings.get("contract", "auto"),
                "llm_client": client,
                "structured_output": False,
                "use_chunking": True,
                "chunk_max_tokens": int(self.settings.get("chunk_max_tokens") or 768),
                "parallel_workers": int(self.settings.get("parallel_workers") or 1),
                "llm_input_format": "markdown",
                "provenance": "detailed",
                "dump_to_disk": False,
                "export_docling": False,
                "export_docling_json": False,
                "export_markdown": False,
                "export_doclang": False,
                "gc_collect": False,
            })
        models = list(getattr(context, "extracted_models", None) or [])
        if not models:
            raise ValidationError("Docling Graph 未返回可校验的课程知识树")
        points: list[dict[str, Any]] = []
        question_ids: set[str] = set()
        excluded_ids: set[str] = set()
        unclassified_ids: set[str] = set()
        for model in models:
            if not isinstance(model, CourseKnowledgeTree):
                try:
                    model = CourseKnowledgeTree.model_validate(model)
                except Exception as exc:
                    raise ValidationError(f"Docling Graph 结果不符合课程知识树 Schema：{exc}") from exc
            question_ids.update(str(value) for value in model.question_block_ids)
            excluded_ids.update(str(value) for value in model.excluded_block_ids)
            unclassified_ids.update(str(value) for value in model.unclassified_block_ids)
            for chapter in model.chapters:
                for section in chapter.sections:
                    for point in section.knowledge_points:
                        source_ids = list(dict.fromkeys(str(value) for value in point.source_block_ids))
                        invalid_ids = [value for value in source_ids if value not in block_map]
                        if invalid_ids:
                            raise ValidationError(
                                f"知识点“{point.title}”引用了不存在的原文块：{', '.join(invalid_ids[:5])}"
                            )
                        if not source_ids:
                            raise ValidationError(f"知识点“{point.title}”没有原文来源")
                        source_text = _normalized("\n".join(
                            str(block_map[value].get("markdown") or block_map[value].get("latex")
                                or block_map[value].get("plain_text") or "")
                            for value in source_ids
                        ))
                        quotes = [str(value).strip() for value in point.evidence_quotes if str(value).strip()]
                        if not quotes or not any(_normalized(quote) in source_text for quote in quotes):
                            raise ValidationError(f"知识点“{point.title}”的证据不能在所引用原文块中逐字定位")
                        points.append({
                            "point_key": f"docling-{len(points) + 1}",
                            "chapter": chapter.title,
                            "section": section.title,
                            "title": point.title,
                            "keywords": point.keywords,
                            "block_ids": source_ids,
                            "evidence_quotes": quotes,
                        })
        known_ids = set(block_map)
        for label, values in (
            ("question_block_ids", question_ids),
            ("excluded_block_ids", excluded_ids),
            ("unclassified_block_ids", unclassified_ids),
        ):
            invalid = values - known_ids
            if invalid:
                raise ValidationError(f"Docling Graph 的 {label} 包含不存在的块：{', '.join(sorted(invalid)[:5])}")
        if not points:
            raise ValidationError("Docling Graph 没有生成任何带原文证据的知识点")
        knowledge_ids = {block_id for point in points for block_id in point["block_ids"]}
        overlaps = (question_ids & excluded_ids) | (question_ids & knowledge_ids) | (excluded_ids & knowledge_ids)
        if overlaps:
            raise ValidationError(f"同一原文块被分配到冲突类别：{', '.join(sorted(overlaps)[:5])}")
        classifications: list[dict[str, Any]] = []
        for block_id in block_map:
            if block_id in question_ids:
                destination, role, reason = "question_bank", "question", "Docling Graph 识别为题目或答案"
            elif block_id in excluded_ids:
                destination, role, reason = "excluded", "non_knowledge", "Docling Graph 识别为非知识正文"
            elif block_id in knowledge_ids:
                destination, role, reason = "knowledge", "source_markdown", "被带证据的知识点引用"
            else:
                destination, role, reason = "unclassified", "teacher_review", "未被可靠归类，保留给教师复核"
            classifications.append({
                "block_id": block_id,
                "destination": destination,
                "semantic_role": role,
                "question_group_key": "",
                "confidence": None,
                "reason": reason,
            })
        # Reading the deprecated package version must not import its
        # Docling/Transformers/Torch dependency chain on Windows.
        version = "test-runner" if self._runner is not None else "deprecated"
        return {
            "classifications": classifications,
            "knowledge_points": points,
            "analyzer_version": f"docling-graph-{version}",
            "prompt_version": "teacher-course-tree-v2",
            "provenance_resolution": getattr(getattr(context, "provenance", None), "resolution", "block-marker"),
        }
