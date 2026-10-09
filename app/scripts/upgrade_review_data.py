"""Explicit, backed-up upgrade. Run with services stopped; never invoked at import/startup."""
import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migrations import apply_migrations
from published_knowledge import capture_publication, capture_question_publication


def upgrade(path):
    with sqlite3.connect(path,timeout=60) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        apply_migrations(conn)
        for row in conn.execute('SELECT version_id FROM knowledge_versions WHERE snapshot_ready=0').fetchall():
            capture_publication(conn,row[0])
        for row in conn.execute('SELECT version_id FROM question_bank_versions').fetchall():
            capture_question_publication(conn,row[0])
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('数据库完整性检查失败')
        if conn.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('数据库外键检查失败')
        return {name:conn.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                for name in ('courses','course_documents','course_questions','course_attempts','published_knowledge_items')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--backup-dir',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    path = args.database.resolve(strict=True)
    args.backup_dir.mkdir(parents=True,exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup = args.backup_dir / f'{path.stem}-{stamp}.sqlite'
    validation = args.backup_dir / f'{path.stem}-{stamp}-validation.sqlite'
    with sqlite3.connect(f'{path.as_uri()}?mode=ro',uri=True) as source:
        with sqlite3.connect(backup) as target:
            source.backup(target)
    with sqlite3.connect(backup) as source, sqlite3.connect(validation) as target:
        source.backup(target)
    checked = upgrade(validation)
    actual = upgrade(path) if args.apply else None
    print(json.dumps({'backup':str(backup),'validation':str(validation),'checked':checked,'applied':actual},ensure_ascii=True))


if __name__ == '__main__':
    main()
