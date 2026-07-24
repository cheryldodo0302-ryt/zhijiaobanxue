from agent_service import CampusAgentService
from campus_service import CampusService
from database import LearningDatabase
from llm_provider import LLMProvider
from skills.qa import guide_question


class GuidedProvider(LLMProvider):
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        if "苏格拉底式助教" in system_prompt:
            return "先从规范化要解决的数据问题入手，你认为重复存储最容易带来什么后果？"
        return "规范化通过分解关系模式减少数据冗余，并降低更新异常。"


class UnsafeGuidanceProvider(LLMProvider):
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        if "苏格拉底式助教" in system_prompt:
            return "最终答案是第三范式。"
        return "规范化用于减少数据冗余。"


def guided_campus(tmp_path, provider=None):
    active_provider = provider or GuidedProvider()
    service = CampusService(
        LearningDatabase(tmp_path / "guided.db"),
        tmp_path / "uploads",
        lambda: active_provider,
    )
    course = service.create_course(
        "数据库原理", "personal_course", "student_1", "student",
    )
    service.upload_document(
        course["course_id"], "student_1", "student",
        "规范化.txt", "text/plain",
        (
            "关系数据库规范化通过分解关系模式减少数据冗余。"
            "规范化可以降低插入异常、删除异常和更新异常。"
        ).encode("utf-8"),
    )
    return service, course, active_provider


def test_guided_turns_are_grounded_and_only_reveal_completes(tmp_path):
    service, course, provider = guided_campus(tmp_path)
    retriever = service._retriever(course["course_id"])
    question = "关系数据库规范化为什么能够减少数据冗余？"

    started = guide_question(question, retriever, provider, intent="start")
    assert started.phase == "guiding"
    assert started.expects_response and started.can_reveal
    assert not started.completed and not started.refused
    assert started.evidence
    assert started.reply.endswith("？")

    responded = guide_question(
        question, retriever, provider,
        intent="respond", phase=started.phase,
        student_message="因为相同数据会被重复保存",
        history=[
            {"role": "user", "content": question},
            {"role": "assistant", "content": started.reply},
        ],
        evidence_refs=[item.to_dict() for item in started.evidence],
    )
    assert responded.phase == "guiding"
    assert "原始问题" in provider.calls[-1][1]
    assert "因为相同数据会被重复保存" in provider.calls[-1][1]

    hinted = guide_question(
        question, retriever, provider,
        intent="hint", phase=responded.phase,
        student_message="我不知道",
    )
    assert hinted.phase == "guiding"
    assert hinted.reply.endswith("？")

    revealed = guide_question(
        question, retriever, provider,
        intent="reveal", phase=hinted.phase,
    )
    assert revealed.phase == "revealed"
    assert revealed.completed and not revealed.expects_response
    assert "规范化" in revealed.reply
    assert revealed.evidence == started.evidence


def test_guided_refusal_does_not_call_model(tmp_path):
    service, course, provider = guided_campus(tmp_path)
    result = guide_question(
        "ZXQJ-999 完全无关的问题",
        service._retriever(course["course_id"]),
        provider,
        intent="start",
    )
    assert result.refused and result.completed
    assert result.phase == "closed"
    assert provider.calls == []

    stale_session = guide_question(
        "关系数据库规范化为什么能够减少数据冗余？",
        service._retriever(course["course_id"]),
        provider,
        intent="respond",
        phase="guiding",
        student_message="继续",
        evidence_refs=[{
            "source_file": "另一个课程.txt",
            "section": "伪造章节",
            "text": "伪造证据",
        }],
    )
    assert stale_session.refused and stale_session.completed
    assert provider.calls == []


def test_unsafe_guidance_falls_back_without_leaking_answer(tmp_path):
    service, course, provider = guided_campus(tmp_path, UnsafeGuidanceProvider())
    result = guide_question(
        "关系数据库规范化为什么能够减少数据冗余？",
        service._retriever(course["course_id"]),
        provider,
        intent="start",
    )
    assert result.phase == "guiding"
    assert result.reply.endswith("？")
    assert "最终答案" not in result.reply
    assert "答案是" not in result.reply


def test_agent_guided_intermediate_turns_do_not_pollute_question_history(tmp_path):
    service, course, _provider = guided_campus(tmp_path)
    agent = CampusAgentService(service)
    base = {
        "agent": "student_assistant",
        "action": "course_qa",
        "actor": {"user_id": "student_1", "role": "student"},
        "scope": {"course_id": course["course_id"]},
    }
    question = "关系数据库规范化为什么能够减少数据冗余？"

    start = agent.invoke({
        **base,
        "request_id": "guide-start",
        "input": {
            "question": question,
            "student_message": "",
            "intent": "start",
            "phase": "initial",
            "history": [],
        },
    })
    assert start.status == "success"
    assert start.data["phase"] == "guiding"
    assert service.db.fetch_all("SELECT * FROM course_questions") == []

    respond = agent.invoke({
        **base,
        "request_id": "guide-respond",
        "input": {
            "question": question,
            "student_message": "因为重复数据被拆分了",
            "intent": "respond",
            "phase": "guiding",
            "history": [
                {"role": "user", "content": question},
                {"role": "assistant", "content": start.data["reply"]},
            ],
            "evidence_refs": start.data["sources"],
        },
    })
    assert respond.status == "success"
    assert service.db.fetch_all("SELECT * FROM course_questions") == []

    reveal = agent.invoke({
        **base,
        "request_id": "guide-reveal",
        "input": {
            "question": question,
            "student_message": "请给出答案",
            "intent": "reveal",
            "phase": "guiding",
            "history": [],
            "evidence_refs": start.data["sources"],
        },
    })
    assert reveal.status == "success"
    assert reveal.data["phase"] == "revealed"
    assert reveal.data["persisted"]
    assert reveal.data["question_id"] is not None
    assert len(service.db.fetch_all("SELECT * FROM course_questions")) == 1


def test_legacy_course_qa_contract_still_works(tmp_path):
    service, course, _provider = guided_campus(tmp_path)
    response = CampusAgentService(service).invoke({
        "request_id": "legacy",
        "agent": "student_assistant",
        "action": "course_qa",
        "actor": {"user_id": "student_1", "role": "student"},
        "scope": {"course_id": course["course_id"]},
        "input": {"question": "关系数据库规范化有什么作用？"},
    })
    assert response.status == "success"
    assert "answer" in response.data
    assert "reply" not in response.data
    assert len(service.db.fetch_all("SELECT * FROM course_questions")) == 1
