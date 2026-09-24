"""Anonymize institution labels without publishing the institution's identity.

Historical labels are deployment data, not source-code constants. Configure them
in the untracked ``server.env`` (or process environment) only while migrating a
legacy database. The repository therefore contains no real school or campus
names to disclose.
"""
from __future__ import annotations

import os
import re
from hashlib import sha256


# Kept as an empty compatibility export. Runtime aliases are intentionally
# loaded from private deployment configuration instead of being committed here.
LEGACY_NAMES: dict[str, str] = {}

_ALIAS_SETTINGS = (
    ("ZHIJIAO_LEGACY_SCHOOL_NAMES", "某高校"),
    ("ZHIJIAO_LEGACY_CAMPUS_A_NAMES", "校区A"),
    ("ZHIJIAO_LEGACY_CAMPUS_B_NAMES", "校区B"),
)

# Compatibility fingerprints keep already-uploaded legacy material safe without
# putting the institution names in the public repository. Deployments should
# prefer the private alias settings above for any additional historical labels.
# Keys are (Unicode length, SHA-256 of the legacy label).
_BUILTIN_FINGERPRINTS = {
    (6, "fcc8fcb627bf6103b5d959295044308987945845f60666476b74f92962188fda"): "某高校",
    (4, "de5bd0cc4c0b9f55d72c1f38514087ea1e5b86b4007969660dfb22eccdc95b2c"): "某高校",
    (3, "dc3e165c17b4aa4286809d064487453edaa0b5a5feb83f62a1c068c754be3236"): "某高校",
    (26, "5c6acb066d4e23d08a125505ec5b13af3bc60f8aa2696196b17282ddbfb0cdd0"): "某高校",
    (2, "f14231e7fccffd0aeff99f75c78e1aa55084ec3dd1584493b8b86d09edebb8f0"): "校区A",
    (2, "b69ab837ad2b2cc69ab8911678c52deceee2b9a5ea59933a26eca8a6d583ebea"): "校区B",
}


def _configured_value(name: str) -> str:
    """Read a legacy alias setting without introducing a config import cycle."""
    try:
        from config import get_runtime_setting

        return get_runtime_setting(name, "")
    except Exception:  # pragma: no cover - only used during minimal bootstrap
        return os.environ.get(name, "").strip()


def _split_aliases(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,;\n]", value or "") if part.strip()]


def legacy_name_map() -> dict[str, str]:
    """Return aliases supplied by the private deployment environment."""
    result: dict[str, str] = {}
    for setting, replacement in _ALIAS_SETTINGS:
        for alias in _split_aliases(_configured_value(setting)):
            if alias != replacement:
                result[alias] = replacement
    return result


def _replace_builtin_fingerprints(value: str) -> str:
    result = value
    lengths = sorted({length for length, _digest in _BUILTIN_FINGERPRINTS}, reverse=True)
    for length in lengths:
        index = 0
        while index <= len(result) - length:
            digest = sha256(result[index:index + length].encode("utf-8")).hexdigest()
            replacement = _BUILTIN_FINGERPRINTS.get((length, digest))
            if replacement is None:
                index += 1
                continue
            result = result[:index] + replacement + result[index + length:]
            index += len(replacement)
    return result


def anonymize_text(value: str) -> str:
    result = value
    # Replace longer aliases first so a short alias cannot partially consume a
    # more specific configured label.
    for old, new in sorted(legacy_name_map().items(), key=lambda item: len(item[0]), reverse=True):
        if old.isascii():
            result = re.sub(re.escape(old), new, result, flags=re.IGNORECASE)
        else:
            result = result.replace(old, new)
    return _replace_builtin_fingerprints(result)


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
    return anonymize_text(value) != value
