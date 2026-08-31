from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

from campus_service import ValidationError


def _json_result(raw: str) -> Any:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I | re.S)
    starts = [position for position in (cleaned.find("{"), cleaned.find("[")) if position >= 0]
    if starts:
        cleaned = cleaned[min(starts):]
    try:
        value, _end = json.JSONDecoder(strict=False).raw_decode(cleaned)
        return value
    except json.JSONDecodeError as exc:
        hint = "（响应疑似被截断）" if exc.pos >= max(0, len(cleaned) - 20) else ""
        raise ValidationError(f"大模型返回格式不符合要求{hint}：{raw[:240]}") from exc


class SemanticKnowledgeService:
    """Shared AI semantic chunking used by private student and shared teacher courses."""

    def __init__(self, provider_factory: Callable[[], Any]):
        self.provider_factory = provider_factory

    def preflight(self) -> None:
        """Validate provider availability before consuming an API call."""
        provider = self.provider_factory()
        if provider is None or not any(
            callable(getattr(provider, name, None)) for name in ("generate", "generate_json")
        ):
            raise ValidationError("AI Provider 不可用，知识树分析尚未发起任何 API 调用")

    @staticmethod
    def _source_meta(row: dict[str, Any]) -> dict[str, Any]:
        raw = row.get("raw")
        if not isinstance(raw, dict):
            try:
                raw = json.loads(row.get("raw_payload_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                raw = {}
        if not isinstance(raw, dict) or str(raw.get("source_kind") or "").lower() != "pptx":
            return {}
        return {
            "source_kind": "pptx",
            "slide_number": raw.get("ppt_slide_number") or row.get("page_number"),
            "slide_title": raw.get("ppt_slide_title") or "",
            "is_slide_title": bool(raw.get("is_slide_title_block")),
            "title_number": raw.get("ppt_title_number") or "",
            "title_level_hint": raw.get("ppt_title_level_hint") or 0,
            "ambiguous_numbering": bool(raw.get("ppt_title_number_ambiguous")),
        }

    def _generate_json(self, system: str, prompt: str, on_call: Callable[[], None] | None = None) -> Any:
        errors: list[str] = []
        for attempt in range(3):
            try:
                if on_call:
                    on_call()
                provider = self.provider_factory()
                if provider is None:
                    raise RuntimeError("AI Provider 不可用")
                generate_json = getattr(provider, "generate_json", None)
                if callable(generate_json):
                    return generate_json(system, prompt)
                return _json_result(provider.generate(system, prompt))
            except Exception as exc:
                errors.append(str(exc))
                lowered = str(exc).lower()
                if any(marker in lowered for marker in (
                    "timeout", "timed out", "readtimeout", "connectionerror",
                    "无法连接智能服务", "qwen_connection_failed",
                )):
                    # The outer semantic job will preserve its checkpoint and
                    # schedule a delayed retry. Repeating the same long request
                    # three times here only turns one outage into a 4-5 minute stall.
                    raise ValidationError(
                        f"{exc}；智能服务暂时超时，教师知识分析将保存进度并等待自动续跑"
                    ) from exc
                if any(marker in lowered for marker in (
                    "http 401", "http 403", "quota exhausted",
                    "insufficient_quota", "invalid api key",
                    "unauthorized", "forbidden",
                )):
                    raise ValidationError(
                        f"{exc}；该错误属于 API Key、权限或额度问题，系统不会重复消耗三次请求。"
                        "请更换可用 Key/模型，或选择“仅本地分析”后重新分析。"
                    ) from exc
                if any(marker in lowered for marker in (
                    "json", "格式不符合", "响应缺少", "未返回有效",
                )):
                    raise ValidationError(
                        f"{exc}；结构化输出不完整，将由教师知识分析按原文安全降级"
                    ) from exc
                if attempt < 2:
                    time.sleep(0.25 * (2 ** attempt))
        raise ValidationError("AI 分析连续失败 3 次：" + " | ".join(errors)[-900:])

    def semantic_chunks(self, rows: list[dict[str, Any]], *, on_call: Callable[[], None] | None = None) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        for start in range(0, len(rows), 8):
            batch = rows[start:start + 8]
            source = "\n\n".join(
                f"[来源:{row.get('section') or '正文'}]\n{row.get('content') or ''}" for row in batch
            )[:14000]
            value = self._generate_json(
                "你是学习材料语义分块专家。只输出合法 JSON 数组，不要 Markdown。每项必须包含 title、keywords、content；内容必须忠实原文。",
                "请按逻辑段落和关键词密度划分适合独立学习的知识块，标题清晰，keywords 为3-8个核心词。\n\n" + source,
                on_call,
            )
            if not isinstance(value, list):
                raise ValidationError("大模型没有返回语义知识块数组")
            for item in value:
                if isinstance(item, dict) and str(item.get("content") or "").strip():
                    created.append({
                        "title": str(item.get("title") or "知识点").strip()[:160],
                        "keywords": [str(x).strip() for x in item.get("keywords", []) if str(x).strip()][:12],
                        "content": str(item.get("content") or "").strip(),
                    })
        return created

    def analyze_document_batch(self, blocks: list[dict[str, Any]], context: list[dict[str, Any]],
                               *, on_call: Callable[[], None] | None = None) -> dict[str, Any]:
        compact = [{
            "block_id": row["block_id"], "type": row["block_type"], "page": row["page_number"],
            "order": row["block_order"], "content": (row["markdown"] or row["latex"] or row["plain_text"])[:2800],
            **self._source_meta(row),
        } for row in blocks]
        previous = [{
            "page": x["page_number"], "content": (x["markdown"] or x["plain_text"])[:500],
            **self._source_meta(x),
        } for x in context[-3:]]
        prompt = {
            "previous_context": previous,
            "blocks": compact,
            "required_output": {
                "classifications": [{"block_id": "", "destination": "knowledge|question_bank|excluded", "semantic_role": "", "question_group_key": "", "confidence": 0.0, "reason": ""}],
                "knowledge_points": [{
                    "point_key": "", "chapter": "", "section": "", "title": "",
                    "keywords": [], "block_ids": [], "evidence_quotes": [],
                }],
            },
        }
        result = self._generate_json(
            "你是教师课程知识库结构分析器。输入是已经完成 OCR/原生解析并落库的 Markdown/公式块，不要重新 OCR。"
            "先区分知识正文、题目答案和装饰内容，再把知识正文组织为章节、分节和可独立学习的知识点。"
            "对于 PPTX，is_slide_title=true 的标题栏是知识边界；连续相同 slide_title 必须归入同一知识点，"
            "不同标题不得混入同一知识点，且知识点顺序必须严格按 slide_number 递增。"
            "标题序列如 1.2 后接 1.3 时必须保持同级和原顺序；孤立的 3 或 3. 可能是章标题、当前 1.3 下的子标题或第三条，"
            "必须结合前后 slide_title 与正文判断，不能只按数字位数升级为章。"
            "原文有 x.x 二级标题时，其下 x.x.x 标题和正文必须并为一个知识点，不得再拆分。"
            "对于教学大纲，‘教学内容’‘目标与要求’‘教学方法’‘考核形式’等重复字段只是实验/任务的属性，"
            "必须并入最近的实验、实训、项目、任务或模块，不得把字段名本身创建为知识点标题。"
            "章节编号必须按教材出现顺序单调前进；正文中的‘2.1节提出’‘见3.2节’等章节引用不是标题，禁止据此新建知识点。"
            "原文没有标题时可以按语义补充简洁标题；knowledge_points 的 block_ids 必须来自输入且只引用支撑该知识点的原文。"
            "不要生成摘要或知识正文。每个知识点必须给出来源 block_id 和至少一段可在来源块逐字找到的 evidence_quote。"
            "每个输入 block_id 最多分类一次；无法可靠判断的块必须标记为 unclassified。"
            "只返回紧凑的合法 JSON 对象，不要代码围栏或解释。reason 不超过40字，"
            "每条 evidence_quote 不超过80字，每批 knowledge_points 不超过12项；"
            "禁止重复原文、长篇解释或输出 required_output 之外的字段。",
            json.dumps(prompt, ensure_ascii=False), on_call,
        )
        if not isinstance(result, dict):
            raise ValidationError("文档结构分析必须返回 JSON 对象")
        classifications = result.get("classifications")
        if not isinstance(classifications, list):
            raise ValidationError("AI 响应缺少 classifications 数组")
        points = result.get("knowledge_points")
        if points is None:
            points = []
        if not isinstance(points, list):
            raise ValidationError("AI 响应中的 knowledge_points 必须是数组")
        expected_order = [str(row["block_id"]) for row in blocks]
        expected_ids = set(expected_order)
        normalized: dict[str, dict[str, Any]] = {}
        valid_destinations = {"knowledge", "question_bank", "excluded", "unclassified"}
        for item in classifications:
            if not isinstance(item, dict):
                continue
            block_id = str(item.get("block_id") or "")
            if block_id not in expected_ids or block_id in normalized:
                continue
            destination = str(item.get("destination") or "unclassified")
            if destination not in valid_destinations:
                destination = "unclassified"
            try:
                confidence = max(0.0, min(1.0, float(item.get("confidence", 0))))
            except (TypeError, ValueError):
                confidence = 0.0
            normalized[block_id] = {
                "block_id": block_id,
                "destination": destination,
                "semantic_role": str(item.get("semantic_role") or "teacher_review")[:64],
                "question_group_key": str(item.get("question_group_key") or "")[:100],
                "confidence": confidence,
                "reason": str(item.get("reason") or "AI 未提供分类理由")[:500],
            }
        for block_id in expected_order:
            if block_id in normalized:
                continue
            normalized[block_id] = {
                "block_id": block_id,
                "destination": "unclassified",
                "semantic_role": "ai_omitted_teacher_review",
                "question_group_key": "",
                "confidence": 0.0,
                "reason": "AI 未返回该块；保留原文并交由教师复核",
            }
        classifications = [normalized[block_id] for block_id in expected_order]
        clean_points: list[dict[str, Any]] = []
        used_ids: set[str] = set()
        block_map = {str(row["block_id"]): row for row in blocks}
        for index, item in enumerate(points):
            if not isinstance(item, dict):
                continue
            source_ids = [
                str(block_id) for block_id in item.get("block_ids", [])
                if str(block_id) in expected_ids and str(block_id) not in used_ids
            ]
            if not source_ids:
                continue
            source_text = re.sub(r"\s+", "", "\n".join(
                str(block_map[block_id].get("markdown") or block_map[block_id].get("latex")
                    or block_map[block_id].get("plain_text") or "")
                for block_id in source_ids
            ))
            evidence = [str(value).strip() for value in item.get("evidence_quotes", []) if str(value).strip()]
            if not evidence or not any(re.sub(r"\s+", "", quote) in source_text for quote in evidence):
                raise ValidationError(
                    f"知识点“{item.get('title') or index + 1}”的证据不能在来源块中逐字定位"
                )
            used_ids.update(source_ids)
            clean_points.append({
                "point_key": str(item.get("point_key") or f"point-{index + 1}"),
                "chapter": str(item.get("chapter") or "未分章").strip()[:180],
                "section": str(item.get("section") or "未分节").strip()[:180],
                "title": str(item.get("title") or "待命名知识点").strip()[:180],
                "keywords": [str(value).strip() for value in item.get("keywords", []) if str(value).strip()][:12],
                "block_ids": source_ids,
                "evidence_quotes": evidence,
            })
        result["knowledge_points"] = clean_points
        result["classifications"] = classifications
        return result

    def resolve_presentation_outline(
        self, groups: list[dict[str, Any]], *,
        on_call: Callable[[], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Resolve PPT title hierarchy without allowing the model to regroup it."""
        payload_groups = [{
            "group_id": row["point_key"], "source_order": index + 1,
            "title": row["title"], "pages": row.get("pages", []),
            "number": row.get("title_number", ""),
            "local_level_hint": row.get("title_level_hint", 2),
            "ambiguous_numbering": bool(row.get("ambiguous_numbering")),
            "evidence_excerpt": str(row.get("evidence_excerpt") or "")[:600],
        } for index, row in enumerate(groups)]
        result = self._generate_json(
            "你是 PPT 课程标题层级判定器。标题分组和幻灯片顺序已经由程序锁定，你只能判定层级，"
            "不得合并、拆分、遗漏、重命名或重排 group。1.2 后接 1.3 通常为同级；"
            "孤立的 3/3. 必须结合前后标题和 evidence_excerpt 判断：若是第三条或当前 1.3 的展开，保留在当前节；"
            "只有标题语义与后续 3.1/3.2 等序列共同支持时才提升为第3章。只返回合法 JSON。",
            json.dumps({
                "presentation_groups": payload_groups,
                "required_output": {"outline": [{
                    "group_id": "", "hierarchy_level": "chapter|section|point",
                    "chapter": "", "section": "", "reason": "",
                }]},
            }, ensure_ascii=False),
            on_call,
        )
        if not isinstance(result, dict) or not isinstance(result.get("outline"), list):
            raise ValidationError("PPT 标题层级分析响应缺少 outline 数组")
        items = [item for item in result["outline"] if isinstance(item, dict)]
        expected = [str(row["point_key"]) for row in groups]
        returned = [str(item.get("group_id") or "") for item in items]
        if returned != expected:
            raise ValidationError("PPT 标题层级分析改变了标题分组或幻灯片顺序")
        resolved: list[dict[str, Any]] = []
        for source, item in zip(groups, items):
            level = str(item.get("hierarchy_level") or "point")
            if level not in {"chapter", "section", "point"}:
                level = "point"
            resolved.append({
                **source,
                "chapter": str(item.get("chapter") or source.get("chapter") or "PPT 课程结构").strip()[:180],
                "section": str(item.get("section") or source.get("section") or "PPT 标题序列").strip()[:180],
                "hierarchy_level": level,
                "hierarchy_reason": str(item.get("reason") or "")[:240],
            })
        return resolved

    def reduce_document_outline(self, candidates: list[dict[str, Any]], *,
                                on_call: Callable[[], None] | None = None) -> dict[str, Any]:
        if not candidates:
            raise ValidationError("AI 没有生成可归并的知识点候选")
        if len(candidates) > 24:
            combined: list[dict[str, Any]] = []
            for start in range(0, len(candidates), 24):
                reduced = self.reduce_document_outline(
                    candidates[start:start + 24], on_call=on_call
                )
                for point in reduced["knowledge_points"]:
                    combined.append({
                        **point,
                        "point_key": f"batch-{start // 24 + 1}-{point['point_key']}",
                    })
            return {"knowledge_points": combined}
        compact = [{
            "candidate_id": row["candidate_id"], "chapter": row["chapter"],
            "section": row["section"], "title": row["title"],
            "keywords": row.get("keywords", []), "pages": row.get("pages", []),
            "source_order": index + 1,
        } for index, row in enumerate(candidates)]
        result = self._generate_json(
            "你是课程文档目录归并器。将分批候选合并为整篇文档的章节、分节和知识点。"
            "可以调整标题和层级、合并同义候选，但不得生成摘要或正文，不得引用不存在的 candidate_id。"
            "输出点必须按其最早 source_order 升序，禁止改变教材或幻灯片的原始先后顺序。"
            "只返回合法 JSON。",
            json.dumps({
                "candidates": compact,
                "required_output": {"points": [{
                    "point_key": "", "chapter": "", "section": "", "title": "",
                    "keywords": [], "source_candidate_ids": [],
                }]},
            }, ensure_ascii=False), on_call,
        )
        if not isinstance(result, dict) or not isinstance(result.get("points"), list):
            raise ValidationError("文档目录归并响应缺少 points 数组")
        candidate_map = {row["candidate_id"]: row for row in candidates}
        used: set[str] = set()
        points: list[dict[str, Any]] = []
        for index, item in enumerate(result["points"]):
            if not isinstance(item, dict):
                continue
            source_candidate_ids = list(dict.fromkeys(
                str(value) for value in item.get("source_candidate_ids", [])
                if str(value) in candidate_map and str(value) not in used
            ))
            if not source_candidate_ids:
                continue
            used.update(source_candidate_ids)
            sources = [candidate_map[value] for value in source_candidate_ids]
            points.append({
                "point_key": str(item.get("point_key") or f"reduced-{index + 1}"),
                "source_candidate_ids": source_candidate_ids,
                "chapter": str(item.get("chapter") or sources[0]["chapter"]).strip()[:180],
                "section": str(item.get("section") or sources[0]["section"]).strip()[:180],
                "title": str(item.get("title") or sources[0]["title"]).strip()[:180],
                "keywords": list(dict.fromkeys(
                    str(value).strip() for value in item.get("keywords", []) if str(value).strip()
                ))[:12],
                "block_ids": list(dict.fromkeys(
                    block_id for source in sources for block_id in source["block_ids"]
                )),
                "evidence_quotes": list(dict.fromkeys(
                    quote for source in sources for quote in source.get("evidence_quotes", [])
                )),
            })
        if used != set(candidate_map):
            missing = sorted(set(candidate_map) - used)
            raise ValidationError(f"文档目录归并遗漏候选：{', '.join(missing[:5])}")
        if not points:
            raise ValidationError("AI 未生成整篇文档目录")
        position = {row["candidate_id"]: index for index, row in enumerate(candidates)}
        points.sort(key=lambda point: min(
            position.get(source_id, len(position))
            for source_id in point.get("source_candidate_ids", [])
        ) if point.get("source_candidate_ids") else len(position))
        return {"knowledge_points": points}

    def unify_course_outline(self, points: list[dict[str, Any]], *, on_call: Callable[[], None] | None = None) -> dict[str, Any]:
        if len(points) > 24:
            combined_points: list[dict[str, Any]] = []
            combined_relations: list[dict[str, Any]] = []
            for start in range(0, len(points), 24):
                batch_number = start // 24 + 1
                unified = self.unify_course_outline(points[start:start + 24], on_call=on_call)
                key_map: dict[str, str] = {}
                for index, point in enumerate(unified.get("points") or [], 1):
                    old_key = str(point.get("course_key") or f"point-{index}")
                    new_key = f"batch-{batch_number}-{old_key}"
                    key_map[old_key] = new_key
                    combined_points.append({**point, "course_key": new_key})
                for relation in unified.get("relations") or []:
                    source_key = key_map.get(str(relation.get("source_course_key") or ""))
                    target_key = key_map.get(str(relation.get("target_course_key") or ""))
                    if source_key and target_key:
                        combined_relations.append({
                            **relation,
                            "source_course_key": source_key,
                            "target_course_key": target_key,
                        })
            return {"points": combined_points, "relations": combined_relations}
        payload = [{
            "source_node_id": row["node_id"], "document_id": row["document_id"],
            "material_type": row.get("material_type", "other"),
            "chapter": row.get("chapter_title", ""), "section": row.get("section_title", ""),
            "title": row["title"], "keywords": row.get("keywords", []),
            "source_order": index + 1,
        } for index, row in enumerate(points)]
        result = self._generate_json(
            "你是课程知识架构师。输入只属于同一种材料用途；只能在该材料分区内合并多份资料，"
            "不得跨材料类型归并。同义知识点可合并，同名异义必须分开。"
            "所有输出知识点必须按其最早 source_order 升序，不能把 1.3 排到 1.2 前。"
            "分析并列、前置、后续、相关、易混淆关系。只返回合法JSON。",
            json.dumps({
                "source_points": payload,
                "required_output": {
                    "points": [{"course_key": "", "chapter": "", "section": "", "title": "", "keywords": [], "source_node_ids": []}],
                    "relations": [{"source_course_key": "", "target_course_key": "", "type": "parallel|prerequisite|follow_up|related|confusable", "confidence": 0.0, "reason": ""}],
                },
            }, ensure_ascii=False), on_call,
        )
        if not isinstance(result, dict):
            raise ValidationError("课程目录合并必须返回 JSON 对象")
        suggestions = result.get("points")
        if not isinstance(suggestions, list):
            raise ValidationError("课程目录合并响应缺少 points 数组")
        expected_ids = {str(row["node_id"]) for row in points}
        returned_ids = {
            str(value)
            for item in suggestions if isinstance(item, dict)
            for value in item.get("source_node_ids", [])
            if str(value) in expected_ids
        }
        if returned_ids != expected_ids:
            raise ValidationError("课程目录归并遗漏来源知识点")
        return result

    def analyze_relations(self, nodes: list[dict[str, Any]], *,
                          on_call: Callable[[], None] | None = None) -> list[dict[str, Any]]:
        payload = {
            "nodes": [{"node_id": node["node_id"], "title": node["title"]} for node in nodes],
            "required_output": {"relations": [{
                "source_node_id": "", "target_node_id": "",
                "type": "parallel|prerequisite|follow_up|related|confusable",
                "confidence": 0.0, "reason": "",
            }]},
        }
        result = self._generate_json(
            "你只分析给定知识标题之间的关系，不得改写标题、正文或目录。"
            "只返回合法 JSON；没有可靠关系时返回空 relations 数组。",
            json.dumps(payload, ensure_ascii=False), on_call,
        )
        if not isinstance(result, dict) or not isinstance(result.get("relations"), list):
            raise ValidationError("AI 关系分析响应缺少 relations 数组")
        return [item for item in result["relations"] if isinstance(item, dict)]
