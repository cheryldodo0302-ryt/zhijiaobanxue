"""Canonical names used in public school and campus content."""
from __future__ import annotations

LEGACY_NAMES = {
    "温州医科大学": "某高校",
    "温州医大": "某高校",
    "温医大": "某高校",
    "wenzhou medical university": "某高校",
    "本部": "校区A",
    "仁济": "校区B",
}


def anonymize_text(value: str) -> str:
    result = value
    for old, new in LEGACY_NAMES.items():
        if old.isascii():
            import re
            result = re.sub(re.escape(old), new, result, flags=re.IGNORECASE)
        else:
            result = result.replace(old, new)
    return result


def anonymize_value(value):
    """Replace display text recursively without changing paths or identity fields."""
    if isinstance(value, str):
        return anonymize_text(value)
    if isinstance(value, list):
        return [anonymize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: (item if key in {"stored_path", "preview_path", "source_image_path", "sha256", "source_fingerprint"}
                      else anonymize_value(item)) for key, item in value.items()}
    return value


def has_legacy_name(value: str) -> bool:
    lowered = value.lower()
    return any(old.lower() in lowered for old in LEGACY_NAMES)
