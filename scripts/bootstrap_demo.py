from __future__ import annotations

import argparse
import re
import secrets
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auth_service import AuthService
from campus_service import CampusService
from config import DATA_DIR, DB_PATH, MATERIALS_DIR
from database import LearningDatabase


def random_password(length: int = 18) -> str:
    alphabet = string.ascii_letters + string.digits + "-_!"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def main() -> int:
    parser = argparse.ArgumentParser(description="创建完全虚构的本地演示账号和课程")
    parser.add_argument("--if-empty", action="store_true", help="数据库已有用户时不修改任何账号")
    args = parser.parse_args()
    db = LearningDatabase(DB_PATH)
    auth = AuthService(db)
    campus = CampusService(db)
    credential_file = DATA_DIR / "demo_credentials.txt"
    if args.if_empty and db.fetch_one("SELECT 1 ok FROM users LIMIT 1"):
        text = credential_file.read_text(encoding="utf-8") if credential_file.exists() else ""
        teacher_match = re.search(r"教师：demo_teacher\s+密码：(\S+)", text)
        student_match = re.search(r"学生：demo_student\s+密码：(\S+)", text)
        teacher = db.fetch_one("SELECT password_hash FROM users WHERE user_id='demo_teacher_001'")
        student = db.fetch_one("SELECT password_hash FROM users WHERE user_id='demo_student_001'")
        credentials_match = False
        if teacher and student and teacher_match and student_match:
            try:
                credentials_match = bool(
                    auth.passwords.verify(teacher["password_hash"], teacher_match.group(1))
                    and auth.passwords.verify(student["password_hash"], student_match.group(1))
                )
            except Exception:
                credentials_match = False
        if credentials_match:
            campus.seed_demo(MATERIALS_DIR)
            print("[DEMO] 演示账号、密码文件和课程资料一致，无需重置。")
            return 0
        print("[DEMO] 演示账号与本机密码文件不一致，只重建虚构演示账号。")
    teacher_password = random_password()
    student_password = random_password()
    with db.connect() as conn:
        conn.execute("DELETE FROM refresh_tokens WHERE user_id IN ('demo_teacher_001','demo_student_001')")
        conn.execute("DELETE FROM users WHERE user_id IN ('demo_teacher_001','demo_student_001')")
        conn.execute(
            """INSERT INTO users(user_id,username,password_hash,role,display_name,status,must_change_password)
               VALUES('demo_teacher_001','demo_teacher',?,'teacher','演示教师','active',0)""",
            (auth.passwords.hash(teacher_password),),
        )
        conn.execute(
            """INSERT INTO users(user_id,username,password_hash,role,display_name,student_number,status,must_change_password)
               VALUES('demo_student_001','demo_student',?,'student','演示学生','DEMO2026','active',0)""",
            (auth.passwords.hash(student_password),),
        )
    campus.seed_demo(MATERIALS_DIR)
    campus.enroll_student("virtual_ai_101", "demo_teacher_001", "demo_student_001")
    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO terms(term_id,term_name,owner_id) VALUES('term_demo_2026','演示学期','demo_teacher_001')")
        conn.execute(
            """INSERT OR IGNORE INTO classes(class_id,course_id,term_id,class_name,teacher_id)
               VALUES('class_demo_2026','virtual_ai_101','term_demo_2026','演示班','demo_teacher_001')"""
        )
        conn.execute(
            """INSERT OR IGNORE INTO class_memberships(class_id,student_id,anonymous_id,status)
               VALUES('class_demo_2026','demo_student_001','demo_anonymous_001','active')"""
        )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    credential_file.write_text(
        "智教伴学本地虚构演示账号（请勿提交此文件）\n"
        f"教师：demo_teacher  密码：{teacher_password}\n"
        f"学生：demo_student  密码：{student_password}\n",
        encoding="utf-8",
    )
    try:
        credential_file.chmod(0o600)
    except OSError:
        pass
    print("[DEMO] 已创建虚构演示课程和账号。")
    print(f"[DEMO] 账号文件：{credential_file.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
