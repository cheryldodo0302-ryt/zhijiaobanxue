from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable


SYMBOL_ALIASES = {
    "σ": "选择", "π": "投影", "ρ": "重命名", "⋈": "连接", "×": "笛卡尔积",
    "∪": "并集", "∩": "交集", "⊆": "子集", "∈": "属于", "→": "函数依赖",
    "↠": "多值依赖", "∧": "逻辑与", "∨": "逻辑或", "¬": "逻辑非", "∀": "全称量词", "∃": "存在量词",
}


def search_aliases(text: str) -> list[str]:
    return sorted({alias for symbol, alias in SYMBOL_ALIASES.items() if symbol in text})


def formula_anomalies(latex: str) -> list[str]:
    issues: list[str] = []
    if latex.count("{") != latex.count("}"):
        issues.append("latex_braces_unbalanced")
    if latex.count("(") != latex.count(")") or latex.count("[") != latex.count("]"):
        issues.append("latex_delimiters_unbalanced")
    if re.search(r"\\[A-Za-z]+$", latex.strip()):
        issues.append("latex_command_incomplete")
    return issues


def normalize_latex(latex: str) -> str:
    value = latex.strip().strip("$")
    for command in ("mathsf", "mathrm", "mathbf", "mathit", "operatorname"):
        pattern = re.compile(rf"\\{command}\s*\{{([^{{}}]*)\}}")
        while pattern.search(value):
            value = pattern.sub(r"\1", value)
    value = value.replace(r"\left", "").replace(r"\right", "")
    value = re.sub(r"\\(?:[,;:!]|\s+)", "", value)
    return re.sub(r"\s+", "", value)


def _walk_blocks(blocks: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for block in blocks:
        yield block
        children = block.get("blocks") or []
        if isinstance(children, list):
            yield from _walk_blocks(child for child in children if isinstance(child, dict))


def _block_text(block: dict[str, Any]) -> str:
    direct = block.get("content") or block.get("text") or ""
    if direct:
        return str(direct)
    parts: list[str] = []
    for line in block.get("lines") or []:
        for span in line.get("spans") or []:
            value = span.get("content") or span.get("text")
            if value:
                parts.append(str(value))
    return "".join(parts).strip()


def _block_image(block: dict[str, Any]) -> str:
    for line in block.get("lines") or []:
        for span in line.get("spans") or []:
            if span.get("image_path"):
                return str(span["image_path"])
    return str(block.get("image_path") or "")


def mineru_to_blocks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    type_map = {
        "title": "title", "text": "paragraph", "list": "list", "code": "code",
        "table": "table", "table_body": "table", "image": "image", "image_body": "image",
        "interline_equation": "formula", "inline_equation": "formula",
    }
    for page in payload.get("pdf_info") or []:
        page_number = int(page.get("page_idx", 0)) + 1
        source = page.get("para_blocks") or page.get("preproc_blocks") or []
        for block in _walk_blocks(x for x in source if isinstance(x, dict)):
            raw_type = str(block.get("type") or "text")
            block_type = type_map.get(raw_type)
            if not block_type:
                continue
            text = _block_text(block)
            if not text and block_type not in {"image", "table"}:
                continue
            latex = text if block_type == "formula" else ""
            issues = formula_anomalies(latex) if latex else []
            image_value = _block_image(block)
            image_name = Path(image_value).name if image_value else ""
            result.append({
                "block_type": block_type,
                "markdown": text,
                "plain_text": text,
                "latex": latex,
                "page_number": page_number,
                "bbox": block.get("bbox") or [],
                "confidence": block.get("score"),
                "source_method": "mineru_pipeline",
                "source_image_path": (payload.get("_image_paths") or {}).get(image_name, ""),
                "verification_status": "review_required" if issues or block_type in {"formula", "table", "image"} else "auto_verified",
                "search_aliases": search_aliases(text),
                "raw": block,
            })
    return result
