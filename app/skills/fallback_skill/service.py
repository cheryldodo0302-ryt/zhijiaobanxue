from typing import Literal

from pydantic import BaseModel

from skills.contracts import DictOutput, ProjectSkill, SkillContext


class FallbackInput(BaseModel):
    reason: Literal["no_evidence", "model_timeout", "model_unavailable", "offline", "unknown"]


class FallbackSkill(ProjectSkill[FallbackInput, DictOutput]):
    name = "fallback_skill"

    def execute(self, context: SkillContext, payload: FallbackInput) -> DictOutput:
        messages = {
            "no_evidence": "当前课程资料不足，无法给出可靠回答。请补充资料或换用更具体的课程术语。",
            "model_timeout": "智能服务响应超时，本次未生成答案。可以稍后重试。",
            "model_unavailable": "智能服务暂时不可用，课程资料和学习记录没有丢失。",
            "offline": "当前网络不可用。你仍可查看已保存的课程资料和学习记录。",
            "unknown": "操作暂时失败，请稍后重试；如持续出现请联系管理员。",
        }
        return DictOutput(data={"reason": payload.reason, "message": messages[payload.reason]})
