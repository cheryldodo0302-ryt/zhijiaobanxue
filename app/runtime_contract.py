"""Runtime/source fingerprint used to detect a stale local API process."""

from __future__ import annotations

import hashlib
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    paths = list(PROJECT_DIR.glob("*.py")) + list((PROJECT_DIR / "scripts").glob("*.py"))
    for path in sorted(paths, key=lambda item: str(item.relative_to(PROJECT_DIR))):
        if path.name.startswith("test_"):
            continue
        digest.update(str(path.relative_to(PROJECT_DIR)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:20]


# Captured once when the API process imports this module.  A launcher reading
# newer source files will therefore detect that the already-running process is
# stale instead of silently reusing it.
RUNTIME_SOURCE_FINGERPRINT = source_fingerprint()
