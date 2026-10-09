from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auth_service import AuthService  # noqa: E402
from config import DB_PATH  # noqa: E402
from database import LearningDatabase  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="创建智教伴学教师账号")
    parser.add_argument("username")
    parser.add_argument("--display-name", default="")
    args = parser.parse_args()
    password = getpass.getpass("教师密码（至少 10 个字符）：")
    confirm = getpass.getpass("再次输入密码：")
    if password != confirm:
        print("两次密码不一致", file=sys.stderr)
        return 2
    user = AuthService(LearningDatabase(DB_PATH)).create_user(
        args.username, password, "teacher", args.display_name
    )
    print(f"教师账号已创建：{user['username']} ({user['user_id']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
