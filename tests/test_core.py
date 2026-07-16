from pathlib import Path

from database import LearningDatabase
from llm_provider import LLMProvider
from skills.exercise import generate_exercises, grade_exercises
from skills.profile import get_learning_profile, recommend_practice
from skills.qa import answer_question
from skills.retrieval import CourseRetriever


class StubProvider(LLMProvider):
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        evidence = user_prompt.split("【课程证据】", 1)[-1]
        lines = [line.strip() for line in evidence.splitlines()
                 if line.strip() and not line.startswith("来源")]
        return "根据测试课程资料，" + " ".join(lines[:3])[:500]


def test_retrieval_and_grounded_answer():
    materials = Path(__file__).parents[1] / "course_materials"
    result = answer_question("监督学习是什么？", CourseRetriever(materials), StubProvider())
    assert not result.refused
    assert result.evidence
    assert result.evidence[0].source_file.endswith(".md")


def test_refusal_for_unrelated_question():
    materials = Path(__file__).parents[1] / "course_materials"
    result = answer_question("火星旅游票价是多少？", CourseRetriever(materials), StubProvider())
    assert result.refused


def test_exercise_and_database(tmp_path):
    items = generate_exercises("监督学习使用带标签样本。", ["监督学习"])
    assert [x.item_type for x in items] == ["选择题", "选择题", "判断题"]
    grade = grade_exercises(items, [x.answer for x in items])
    assert grade.score == 100.0 and grade.wrong_items == []
    db = LearningDatabase(tmp_path / "test.db")
    qid = db.save_question("问题", "回答", [], ["监督学习"], False)
    db.save_attempt(qid, grade.score, 3, grade.wrong_items, ["监督学习"], grade.records)
    assert len(db.fetch_all("SELECT * FROM practice_attempts")) == 1


def test_profile_uses_recent_and_cumulative_performance(tmp_path):
    db = LearningDatabase(tmp_path / "profile.db")
    for correct in (False, False, False):
        record = {"knowledge_points": ["监督学习"], "correct": correct}
        db.save_attempt(None, 0, 1, [record], ["监督学习"], [record])
    profile = get_learning_profile(db)
    point = profile["weak_points"][0]
    assert point["level"] == "弱"
    assert point["weakness_score"] == 1.0
    assert recommend_practice(profile)["topics"] == ["监督学习"]
