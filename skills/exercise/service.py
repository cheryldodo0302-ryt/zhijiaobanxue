import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class ExerciseItem:
    item_type: str
    question: str
    options: list[str]
    answer: str
    explanation: str
    knowledge_points: list[str]
    difficulty: str = "中等"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GradeResult:
    score: float
    correct_count: int
    total: int
    records: list[dict[str, Any]]
    wrong_items: list[dict[str, Any]]
    topic_stats: dict[str, dict[str, int]]


def _prioritize_points(knowledge_points: list[str], weak_topics: list[dict] | None) -> list[str]:
    allowed = list(dict.fromkeys(knowledge_points or ["课程核心概念"]))
    weak_order = [row["knowledge_point"] for row in (weak_topics or [])
                  if row.get("knowledge_point") in allowed]
    return weak_order + [point for point in allowed if point not in weak_order]


def generate_exercises(answer: str, knowledge_points: list[str],
                       weak_topics: list[dict] | None = None) -> list[ExerciseItem]:
    points = _prioritize_points(knowledge_points, weak_topics)
    p1, p2 = points[0], points[min(1, len(points) - 1)]
    clean_answer = re.sub(r"\[证据\s*#\d+\]", "", answer).replace("\n", " ").strip()
    snippet = clean_answer[:120]
    return [
        ExerciseItem("选择题", f"关于“{p1}”，下列哪项最符合刚才课程答疑的内容？",
                     [snippet, "该知识点与课程资料无关", "课程资料明确否定了全部相关概念", "无法从任何资料中学习该知识点"],
                     snippet, "该表述直接来自当前有证据支持的答疑内容。", [p1], "中等"),
        ExerciseItem("选择题", f"复习“{p2}”时，最可靠的依据是什么？",
                     ["当前课程资料及其证据片段", "未经核实的网络传言", "与课程无关的个人猜测", "随机选择答案"],
                     "当前课程资料及其证据片段", "课程结论应回到原始证据核对。", [p2], "简单"),
        ExerciseItem("判断题", f"判断：当前答疑涉及“{p1}”，复习时应回看所展示的课程来源与证据片段。",
                     ["正确", "错误"], "正确", "来源与证据片段可帮助核对课程概念。", [p1], "简单"),
    ]


def _normalize(value: str | None) -> str:
    value = (value or "").strip().upper()
    judge = {"对": "正确", "是": "正确", "TRUE": "正确", "T": "正确", "√": "正确",
             "错": "错误", "否": "错误", "FALSE": "错误", "F": "错误", "×": "错误"}
    return judge.get(value, re.sub(r"[，,、\s]", "", value))


def grade_exercises(items: list[ExerciseItem], responses: list[str | None]) -> GradeResult:
    records: list[dict[str, Any]] = []
    wrong: list[dict[str, Any]] = []
    topic_stats: dict[str, dict[str, int]] = {}
    for index, item in enumerate(items):
        response = responses[index] if index < len(responses) else None
        correct = _normalize(response) == _normalize(item.answer)
        row = item.to_dict()
        row.update({"qid": index + 1, "student_answer": response or "未作答", "correct": correct})
        records.append(row)
        if not correct:
            wrong.append(row)
        for point in item.knowledge_points:
            stats = topic_stats.setdefault(point, {"answered": 0, "correct": 0, "wrong": 0})
            stats["answered"] += 1
            stats["correct" if correct else "wrong"] += 1
    correct_count = sum(int(row["correct"]) for row in records)
    score = round(correct_count / max(len(items), 1) * 100, 1)
    return GradeResult(score, correct_count, len(items), records, wrong, topic_stats)
