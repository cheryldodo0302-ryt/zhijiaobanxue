import re
from dataclasses import dataclass

from llm_provider import LLMProvider
from skills.retrieval import CourseRetriever, Evidence


@dataclass
class QAResult:
    answer: str
    evidence: list[Evidence]
    knowledge_points: list[str]
    refused: bool


def _format_refusal(question: str, weak_evidence: list[Evidence]) -> str:
    keywords = "、".join(sorted(CourseRetriever._keywords(question), key=len, reverse=True)[:4]) or question
    hint = ""
    if weak_evidence:
        hint = f"\n- 弱相关线索：`{weak_evidence[0].source_file}`｜{weak_evidence[0].section}"
    return ("### 资料不足，无法给出可靠回答\n"
            f"- 已检索关键词：{keywords}\n"
            "- 命中情况：没有找到足以直接支持答案的课程原文。"
            f"{hint}\n"
            "- 建议：换用更具体的课程术语提问，或补充相关章节资料。")


def _extract_points(evidence: list[Evidence]) -> list[str]:
    points = []
    for item in evidence:
        title = re.sub(r"^[第\d一二三四五六七八九十章节、. ]+", "", item.section).strip()
        if title and title not in points:
            points.append(title)
    return points[:3]


def answer_question(question: str, retriever: CourseRetriever, provider: LLMProvider,
                    min_score: float = 0.12, top_k: int = 4) -> QAResult:
    candidates = retriever.search(question, top_k)
    evidence = [item for item in candidates if item.score >= min_score]
    if not evidence:
        return QAResult(_format_refusal(question, candidates), [], [], True)
    context = "\n\n".join(
        f"来源：{e.source_file}｜章节：{e.section}\n{e.text}" for e in evidence
    )
    system = ("你是课程伴学助手。只能依据给定课程证据作答，不得使用外部知识或编造。"
              "只陈述证据直接支持的结论，不得使用‘一般来说’等常识补充。"
              "回答应简洁、准确；证据不足时必须明确拒绝。")
    prompt = f"问题：{question}\n\n【课程证据】\n{context}"
    raw_answer = provider.generate(system, prompt).strip()
    if not raw_answer:
        return QAResult(_format_refusal(question, candidates), [], [], True)
    answer = raw_answer if "[证据 #" in raw_answer else f"{raw_answer} [证据 #1]"
    return QAResult(answer, evidence, _extract_points(evidence), False)
