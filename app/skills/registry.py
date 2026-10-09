from __future__ import annotations

from pathlib import Path


SKILL_IDS = (
    "document_ingestion_skill", "knowledge_retrieval_skill", "course_qa_skill",
    "quiz_generation_skill", "learning_profile_skill", "class_analysis_skill",
    "teaching_report_skill", "permission_guard_skill", "fallback_skill",
)


def manifest_paths(root: Path | None = None) -> list[Path]:
    base = root or Path(__file__).resolve().parent
    return [base / skill_id / "skill_manifest.yaml" for skill_id in SKILL_IDS]


def validate_catalog(root: Path | None = None) -> list[str]:
    return [str(path) for path in manifest_paths(root) if not path.is_file()]
