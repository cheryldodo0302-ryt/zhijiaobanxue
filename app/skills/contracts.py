from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class SkillContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1, max_length=100)
    role: str = Field(pattern="^(student|teacher)$")
    course_id: str = Field(min_length=1, max_length=100)


class SkillFailure(RuntimeError):
    pass


class ProjectSkill(ABC, Generic[InputT, OutputT]):
    """Internal project Skill contract; this is not an official OpenClaw schema."""

    name: str

    def __init__(self) -> None:
        self.logger = logging.getLogger(f"skills.{self.name}")

    def run(self, context: SkillContext, payload: InputT) -> OutputT:
        try:
            return self.execute(context, payload)
        except Exception:
            self.logger.exception("Skill failed: name=%s course=%s user=%s", self.name, context.course_id, context.user_id)
            raise

    @abstractmethod
    def execute(self, context: SkillContext, payload: InputT) -> OutputT:
        raise NotImplementedError


class DictOutput(BaseModel):
    data: dict[str, Any]

