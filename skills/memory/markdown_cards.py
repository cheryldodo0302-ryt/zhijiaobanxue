"""Lossless, local splitting of published Markdown into student study cards."""
import re


def _numbered_level(line: str) -> int | None:
    text = re.sub(r'^ {0,3}#{1,6}\s+', '', line).strip()
    if (len(text) <= 120 and not re.search(r'[。！？；=]', text)
            and re.match(r'^\d+(?:\.\d+){1,}(?![\d.])\s*[^\W\d_]', text)):
        return 0
    if re.match(r'^(?:\d+[.、．](?!\d)|[一二三四五六七八九十]+[、．.])\s*\S', text):
        return 1
    if re.match(r'^[（(](?:\d+|[一二三四五六七八九十]+)[)）]\s*\S', text):
        return 2
    return None


def _merge_heading_only_cards(parts: list[str]) -> list[str]:
    """Attach title-only fragments to real content without dropping source text."""
    cards: list[str] = []
    pending = ''
    for part in parts:
        lines = [line.strip() for line in part.splitlines() if line.strip()]
        only_headings = all(
            re.match(r'^#{1,6}\s+\S', line)
            or _numbered_level(line) == 0
            or re.fullmatch(r'第\s*[0-9一二三四五六七八九十百零〇]+\s*[章节篇部卷](?:\s*[^。！？；]{0,60})?', line)
            for line in lines
        )
        if only_headings:
            pending += part
        else:
            cards.append(pending + part)
            pending = ''
    if pending:
        if cards:
            cards[-1] += pending
        else:
            cards.append(pending)
    return cards


def split_numbered_sections(content: str) -> list[str]:
    """Keep numbered concepts with their explanations and subordinate examples."""
    markers: list[tuple[int, int]] = []
    offset = 0
    fence = ''
    math_end = ''
    for line in content.splitlines(keepends=True):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if fence:
            if re.fullmatch(r' {0,3}' + re.escape(fence[0]) + '{' + str(len(fence)) + r',}\s*', line):
                fence = ''
        elif marker and not math_end:
            fence = marker.group(1)
        else:
            level = _numbered_level(line)
            if not math_end and level is not None and not line.startswith(('    ', '\t')):
                markers.append((offset, level))
            for token in re.findall(r'(?<!\\)(?:\$\$|\$|\\\[|\\\]|\\\(|\\\))', line):
                if math_end:
                    if token == math_end:
                        math_end = ''
                elif token in ('$$', '$', r'\[', r'\('):
                    math_end = {r'\[': r'\]', r'\(': r'\)'}.get(token, token)
        offset += len(line)
    # A section heading starts a new topic; within it choose the outermost
    # numbering style. (1)/(2)/(3) under "2." remain with that parent concept.
    chapters = [position for position, level in markers if level == 0]
    ranges = sorted(set([0, *chapters, len(content)]))
    cuts = {0, len(content)}
    for start, end in zip(ranges, ranges[1:]):
        cuts.add(start)
        items = [(pos, level) for pos, level in markers if start <= pos < end and level > 0]
        if not items:
            continue
        outer = min(level for _, level in items)
        peers = [pos for pos, level in items if level == outer]
        # Attach section introduction to the first concept rather than creating
        # a nearly empty title/intro card.
        cuts.update(peers[1:])
    ordered = sorted(cuts)
    return _merge_heading_only_cards([content[start:end] for start, end in zip(ordered, ordered[1:])])


def markdown_card_title(content: str, fallback: str) -> str:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    for level in (1, 0, 2):
        for line in (reversed(lines) if level == 0 else lines):
            if _numbered_level(line) == level:
                return re.sub(r'^#{1,6}\s+', '', line)[:120]
    return fallback


def split_markdown_cards(content: str, target: int = 800) -> list[str]:
    """Prefer section/paragraph boundaries; oversized structured blocks stay intact."""
    numbered = split_numbered_sections(content)
    if len(numbered) > 1:
        return numbered
    if len(content) <= target:
        return [content]
    units: list[str] = []
    pending = ""
    fence = ""
    math_end = ""
    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        if not fence and not math_end and re.match(r"^ {0,3}#{1,6}\s", line) and pending.strip():
            units.append(pending)
            pending = ""
        pending += line
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", line):
                fence = ""
            continue
        if marker and not math_end:
            fence = marker.group(1)
            continue
        # Track multiline inline/display TeX too, including blank lines inside it.
        for token in re.findall(r"(?<!\\)(?:\$\$|\$|\\\[|\\\]|\\\(|\\\))", line):
            if math_end:
                if token == math_end:
                    math_end = ""
            elif token in ("$$", "$", r"\[", r"\("):
                math_end = {r"\[": r"\]", r"\(": r"\)"}.get(token, token)
        if not stripped and not math_end:
            units.append(pending)
            pending = ""
    if pending:
        units.append(pending)

    # Long unformatted prose can be split at complete sentences. Never slice TeX,
    # lists, tables, links or code just to enforce a character limit.
    expanded: list[str] = []
    for unit in units:
        if len(unit) > target and not re.search(r"[\\$`~|\[\]<>*_]|^\s*(?:#|>|[-+]|\d+[.)])", unit, re.M):
            expanded.extend(re.split(r"(?<=[。！？；])|(?<=[.!?;])(?=\s)", unit))
        else:
            expanded.append(unit)

    cards: list[str] = []
    current = ""
    for unit in expanded:
        heading = bool(re.match(r"^ {0,3}#{1,6}\s", unit))
        # Keep a heading with the paragraph that follows it.
        only_heading = bool(re.fullmatch(r"\s*#{1,6}[^\n]+\s*", current))
        if current.strip() and unit.strip() and not only_heading and (heading or len(current) + len(unit) > target):
            cards.append(current)
            current = ""
        current += unit
    if current:
        cards.append(current)
    return _merge_heading_only_cards(cards) or [content]
