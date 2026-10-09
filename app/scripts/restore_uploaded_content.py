"""Restore text changed by the 2026-09-23 migration without replacing newer rows.

The upload files themselves were never edited. This restores only SQLite values
whose current value still matches the migration's transformation of the backup.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from institution_anonymization import anonymize_text, anonymize_value

SKIP = {"stored_path", "preview_path", "source_image_path", "sha256", "source_fingerprint",
        "content_sha256", "ai_key_encrypted", "file_path", "artifact_path", "manifest_path"}


def transform(value: str, column: str) -> str:
    if column.endswith("_json") or column == "snapshot_json":
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            pass
        else:
            replaced = anonymize_value(decoded)
            if replaced != decoded:
                return json.dumps(replaced, ensure_ascii=False, separators=(",", ":"))
    return anonymize_text(value)


def restore(current_path: Path, original_path: Path, safety_backup: Path, *, dry_run: bool = False) -> dict[str, int]:
    current_path = current_path.resolve()
    original_path = original_path.resolve()
    safety_backup = safety_backup.resolve()
    if not current_path.is_file() or not original_path.is_file():
        raise ValueError("当前数据库或迁移前备份不存在")
    if len({current_path, original_path, safety_backup}) != 3 or safety_backup.exists():
        raise ValueError("备份路径不能与数据库重复，且不能覆盖现有文件")

    counts: dict[str, int] = {}
    skipped: dict[str, int] = {}
    with sqlite3.connect(original_path) as original, sqlite3.connect(current_path) as current:
        if not dry_run:
            safety_backup.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(safety_backup) as backup:
                current.backup(backup)
            current.execute("BEGIN IMMEDIATE")
        for (table,) in original.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            if not current.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                continue
            columns = [row[1] for row in original.execute(f'PRAGMA table_info("{table}")')
                       if str(row[2]).upper() in {"TEXT", "VARCHAR"} or "CHAR" in str(row[2]).upper()]
            current_columns = {row[1] for row in current.execute(f'PRAGMA table_info("{table}")')}
            for column in columns:
                if (column not in current_columns or column in SKIP or column.endswith("_id")
                        or column.endswith("_hash") or column.endswith("_token")):
                    continue
                key = f"{table}.{column}"
                for rowid, before in original.execute(f'SELECT rowid,"{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'):
                    if not isinstance(before, str):
                        continue
                    migrated = transform(before, column)
                    if migrated == before:
                        continue
                    actual_row = current.execute(f'SELECT "{column}" FROM "{table}" WHERE rowid=?', (rowid,)).fetchone()
                    if actual_row is not None and actual_row[0] == before:
                        continue
                    if actual_row is None or actual_row[0] != migrated:
                        skipped[key] = skipped.get(key, 0) + 1
                        continue
                    if not dry_run:
                        current.execute(f'UPDATE "{table}" SET "{column}"=? WHERE rowid=?', (before, rowid))
                    counts[key] = counts.get(key, 0) + 1
        if not dry_run:
            violations = current.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"数据库关联校验失败：{violations[:3]}")
            current.commit()
    return {"restored": counts, "skipped_changed_since_migration": skipped}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(restore(args.db, args.original, args.backup, dry_run=args.dry_run), ensure_ascii=False, indent=2))
