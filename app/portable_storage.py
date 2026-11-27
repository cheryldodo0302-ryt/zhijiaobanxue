"""Repair stored filesystem references when an installation changes location."""
import json
from pathlib import Path


def path_resolver(data_dir):
    data_dir = Path(data_dir).resolve()
    marker = data_dir / ".portable-location.json"
    try:
        roots = json.loads(marker.read_text(encoding="utf-8")).get("roots", [])
    except (OSError, ValueError, AttributeError):
        roots = []
    roots = [str(root).replace("\\", "/").rstrip("/") for root in roots if isinstance(root, str)]
    current = str(data_dir).replace("\\", "/")

    def resolve(value):
        if not isinstance(value, str) or not value:
            return value
        normalized = value.replace("\\", "/")
        suffixes = []
        for root in sorted([current, *roots], key=len, reverse=True):
            if normalized.lower().startswith(root.lower() + "/"):
                suffixes.append(normalized[len(root) + 1:])
        anchor = normalized.lower().rfind("/data/")
        if anchor >= 0:
            suffixes.append(normalized[anchor + 6:])
        if normalized.startswith("data/"):
            suffixes.append(normalized[5:])
        # Older custom data roots were not necessarily named "data".
        anchor = normalized.lower().find("/uploads/")
        if anchor >= 0:
            suffixes.append(normalized[anchor + 1:])
        if normalized.startswith("uploads/"):
            suffixes.append(normalized)
        for suffix in suffixes:
            try:
                target = data_dir.joinpath(*suffix.split("/")).resolve()
                if target.is_relative_to(data_dir) and target.exists():
                    return str(target)
            except (OSError, ValueError):
                continue
        return value

    def remember():
        if current in roots:
            return
        marker.write_text(json.dumps({"roots": [*roots, current]}, ensure_ascii=False, indent=2), encoding="utf-8")

    return resolve, remember


def relocate_json(value, resolve, key=""):
    if isinstance(value, dict):
        return {k: relocate_json(v, resolve, k) for k, v in value.items()}
    if isinstance(value, list):
        return [relocate_json(v, resolve, key) for v in value]
    if isinstance(value, str) and key.endswith(("_path", "_dir", "_root")) and key not in {"chapter_path", "heading_path", "relative_path", "source_relative_path"}:
        return resolve(value)
    return value


def relocate_database_paths(conn, data_dir):
    resolve, remember = path_resolver(data_dir)
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    quote = lambda name: '"' + name.replace('"', '""') + '"'
    for table_row in tables:
        table = quote(str(table_row["name"]))
        columns = conn.execute(f"PRAGMA table_info({table})").fetchall()
        for info in columns:
            name = str(info["name"])
            if not name.endswith(("_path", "_dir", "_root", "_json")) or name in {"relative_path", "source_relative_path"}:
                continue
            column = quote(name)
            for row in conn.execute(f"SELECT rowid AS portable_rowid,{column} AS saved FROM {table} WHERE typeof({column})='text' AND {column}<>''").fetchall():
                old = row["saved"]
                if name.endswith("_json"):
                    try:
                        value = json.loads(old)
                    except ValueError:
                        continue
                    updated = relocate_json(value, resolve)
                    new = json.dumps(updated, ensure_ascii=False) if updated != value else old
                else:
                    new = resolve(old)
                if new != old:
                    conn.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (new, row["portable_rowid"]))
    # Resume checkpoints contain paths too. Touch only structured path fields,
    # leaving original uploads, narrative text, hashes and logical source names intact.
    for path in Path(data_dir).glob("uploads/**/*"):
        if path.name != "manifest.json" or not path.is_file():
            continue
        try:
            original = json.loads(path.read_text(encoding="utf-8"))
            updated = relocate_json(original, resolve)
            if updated != original:
                temporary = path.with_suffix(".portable.tmp")
                temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(path)
        except (OSError, ValueError):
            continue
    remember()
