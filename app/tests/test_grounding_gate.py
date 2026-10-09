from pathlib import Path

import pytest

from campus_service import ChunkRetriever
from llm_provider import LLMProvider
from skills.qa import answer_question, guide_question
from skills.retrieval import CourseRetriever


class RecordingProvider(LLMProvider):
    def __init__(self):
        self.calls = 0

    def generate(self, system_prompt, user_prompt):
        self.calls += 1
        return "请在课程原文中找到监督学习与标签之间的关系。"


@pytest.fixture(params=['files', 'chunks'])
def retriever(request):
    original = CourseRetriever(Path(__file__).parents[1] / 'course_materials')
    if request.param == 'files':
        return original
    return ChunkRetriever([dict(content=r['text'], section=r['section'], original_name=r['source_file'])
                           for r in original.chunks])


@pytest.mark.parametrize('question', [
    '火星旅游票价是多少？', '监督学习的发明者早餐吃什么？',
    '机器学习课程老师的家庭住址是什么？', '数据库能治疗感冒吗？',
    'asdfghjkl123456', '监督学习的最新国际市场价格是多少？',
    '123456789', '？？？',
])
def test_unsupported_questions_never_call_generation(retriever, question):
    provider = RecordingProvider()
    direct = answer_question(question, retriever, provider)
    guided = guide_question(question, retriever, provider)
    assert direct.refused and not direct.evidence
    assert guided.refused and guided.completed and not guided.evidence
    assert provider.calls == 0


@pytest.mark.parametrize('question', ['监督学习是什么？', '什么是关系数据库？'])
def test_supported_questions_still_answer(retriever, question):
    provider = RecordingProvider()
    assert not answer_question(question, retriever, provider).refused
    assert not guide_question(question, retriever, provider).refused
    assert provider.calls >= 2
