from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class OpenClawMappingNotConfigured(RuntimeError):
    pass


class OpenClawAdapter(ABC):
    """Boundary for a future official OpenClaw integration.

    No platform field names or wire format are defined here because the official
    specification has not been supplied in the competition files available to
    this project. Implement this interface only after obtaining and reviewing
    the official contract.
    """

    @abstractmethod
    def map_project_manifest(self, internal_manifest: dict[str, Any]) -> dict[str, Any]:
        raise OpenClawMappingNotConfigured("待获得官方规范后实现字段映射")

    @abstractmethod
    def invoke(self, mapped_request: dict[str, Any]) -> dict[str, Any]:
        raise OpenClawMappingNotConfigured("待获得官方规范后实现调用适配")
