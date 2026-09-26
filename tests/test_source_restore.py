import sqlite3
from hashlib import sha256

import institution_anonymization
from institution_anonymization import anonymize_text
from scripts.restore_uploaded_content import restore


def test_restore_only_migration_values_and_preserve_newer_edits(tmp_path, monkeypatch):
    monkeypatch.setenv("ZHIJIAO_LEGACY_SCHOOL_NAMES", "示例大学,Example University")
    monkeypatch.setenv("ZHIJIAO_LEGACY_CAMPUS_A_NAMES", "旧甲校区")
    monkeypatch.setenv("ZHIJIAO_LEGACY_CAMPUS_B_NAMES", "旧乙校区")
    original = tmp_path / "original.db"
    current = tmp_path / "current.db"
    safety = tmp_path / "safety.db"
    with sqlite3.connect(original) as conn:
        conn.execute("CREATE TABLE documents (original_name TEXT, content TEXT, stored_path TEXT, sha256 TEXT)")
        conn.executemany("INSERT INTO documents VALUES (?,?,?,?)", [
            ("旧乙校区教材.docx", "示例大学 旧甲校区", "/files/旧乙校区教材.docx", "abc"),
            ("旧甲校区课件.pdf", "旧版正文", "/files/旧甲校区课件.pdf", "def"),
        ])
    with sqlite3.connect(original) as conn, sqlite3.connect(current) as copy:
        conn.backup(copy)
    with sqlite3.connect(current) as conn:
        conn.execute("UPDATE documents SET original_name='校区B教材.docx',content='某高校 校区A' WHERE rowid=1")
        conn.execute("UPDATE documents SET original_name='新名称.pdf' WHERE rowid=2")
    result = restore(current, original, safety)
    assert result["restored"] == {"documents.original_name": 1, "documents.content": 1}
    assert result["skipped_changed_since_migration"] == {"documents.original_name": 1}
    with sqlite3.connect(current) as conn:
        assert conn.execute("SELECT * FROM documents WHERE rowid=1").fetchone() == (
            "旧乙校区教材.docx", "示例大学 旧甲校区", "/files/旧乙校区教材.docx", "abc"
        )
        assert conn.execute("SELECT original_name FROM documents WHERE rowid=2").fetchone()[0] == "新名称.pdf"
    assert safety.is_file()
    assert anonymize_text("示例大学") == "某高校"
    assert anonymize_text("example university、EXAMPLE UNIVERSITY") == "某高校、某高校"


def test_fingerprinted_english_alias_ignores_case(monkeypatch):
    alias = "Example Academy"
    monkeypatch.setattr(institution_anonymization, "_BUILTIN_FINGERPRINTS", {
        (len(alias), sha256(alias.encode("utf-8")).hexdigest()): "某高校",
    })
    assert anonymize_text("EXAMPLE ACADEMY、example academy") == "某高校、某高校"
