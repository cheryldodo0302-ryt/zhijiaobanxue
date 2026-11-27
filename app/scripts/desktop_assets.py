"""Track packaged UI freshness without Node.js on a copied installation."""
import hashlib
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"


def fingerprint():
    digest = hashlib.sha256()
    paths = [p for p in (WEB / "src").rglob("*") if p.is_file()]
    paths += [WEB / name for name in ("index.html", "package.json", "package-lock.json", "vite.config.ts")]
    for path in sorted(paths):
        if path.is_file():
            digest.update(path.relative_to(WEB).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def main():
    marker = WEB / "dist" / ".source-fingerprint"
    if not (WEB / "dist" / "index.html").is_file():
        return 1
    value = fingerprint()
    if "--stamp" in sys.argv:
        marker.write_text(value, encoding="ascii")
        return 0
    return 0 if marker.is_file() and marker.read_text(encoding="ascii").strip() == value else 1


if __name__ == "__main__":
    raise SystemExit(main())
