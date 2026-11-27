"""Export this private demonstration installation, including accounts and data."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
import sys
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def export_demo(project: Path, data_dir: Path, destination: Path) -> Path:
    project, data_dir, destination = project.resolve(), data_dir.resolve(), destination.resolve()
    if not (data_dir / "learning.db").is_file():
        raise ValueError("找不到当前账号数据库 learning.db，已停止导出")
    if destination.is_relative_to(project) or destination.is_relative_to(data_dir):
        raise ValueError("导出文件必须位于 app 和数据目录之外")
    if destination.exists():
        raise FileExistsError(f"导出文件已存在：{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    excluded = {".git", ".agents", ".codex", ".aws", ".runtime", "__pycache__", ".pytest_cache",
                "node_modules", "dist-card-preview", "exports", "tmp", "temp"}
    prefix = Path("zhijiao-demo")
    with tempfile.TemporaryDirectory(prefix="zhijiao-export-", dir=destination.parent) as scratch:
        staged = Path(scratch) / "demo.zip"
        with ZipFile(staged, "w", ZIP_DEFLATED, compresslevel=6) as archive:
            # Prune dependency/cache trees before descending: copied venvs are not portable.
            import os
            for directory, folders, files in os.walk(project, followlinks=False):
                folders[:] = [name for name in folders if name not in excluded
                              and not name.startswith((".venv", ".pytest-", ".e2e"))
                              and not (Path(directory) / name).is_symlink()
                              and (Path(directory) / name).resolve() != data_dir
                              and (Path(directory) / name) != project / "data"]
                for name in files:
                    source = Path(directory) / name
                    relative = source.relative_to(project)
                    if source.is_symlink() or source.name == "server.env" or source.suffix in {".log", ".pyc", ".tmp", ".bak"}:
                        continue
                    archive.write(source, str(prefix / "app" / relative))
            # Force the exported copy to use its bundled data, including for an external source data dir.
            env_path = project / "server.env"
            content = env_path.read_text(encoding="utf-8-sig") if env_path.exists() else ""
            lines = [line for line in content.splitlines() if line.split("=", 1)[0].strip() != "ZHIJIAO_DATA_DIR"]
            archive.writestr(str(prefix / "app" / "server.env"), "\n".join([*lines, "ZHIJIAO_DATA_DIR=data", ""]))
            for index, source in enumerate(sorted(data_dir.rglob("*"))):
                if not source.is_file() or source.is_symlink() or any(part.is_symlink() for part in source.parents):
                    continue
                if source.name.endswith(("-wal", "-shm", "-journal")):
                    continue
                target = str(prefix / "app" / "data" / source.relative_to(data_dir))
                if source.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                    snapshot = Path(scratch) / f"snapshot-{index}.db"
                    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as origin, closing(sqlite3.connect(snapshot)) as backup:
                        origin.backup(backup)
                        if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise ValueError(f"数据库校验失败：{source.name}")
                    archive.write(snapshot, target)
                else:
                    archive.write(source, target)
            for source in project.parent.iterdir():
                if source.is_file() and source.suffix.lower() in {".cmd", ".exe"}:
                    archive.write(source, str(prefix / source.name))
            installers = project.parent / "installers"
            if installers.is_dir():
                for source in installers.rglob("*"):
                    if source.is_file() and not source.is_symlink():
                        archive.write(source, str(prefix / "installers" / source.relative_to(installers)))
            archive.writestr(str(prefix / "迁移说明.txt"),
                "这是当前演示系统的私有副本，包含现有账号、学生数据、上传资料和本地配置。\n"
                "在新电脑完整解压，双击 智教伴学.exe；也可运行 app/start.cmd。\n"
                "继续使用原账号和原密码。新导入学生的账号和初始密码均为本人学号，首次登录需修改密码。\n"
                "保留 app/web/dist 与 installers；启动器会自动准备 Python，无需预装 Node.js；首次安装依赖需联网。\n"
                "OpenClaw：双击 启动OpenClaw.cmd，自动复用知识库 API 分析的接口、模型与密钥。\n"
                "必须保留完整 app/data（含隐藏的 .job-secret.key）；新电脑会自动生成独立的 OpenClaw 本机配置。\n"
                "导出包不包含 .runtime 缓存，首次使用 OpenClaw 会从随附便携包准备 Node.js，并联网安装 OpenClaw。\n"
                "远程 AI、MinerU 等功能仍使用原配置，需要新电脑能访问对应服务。\n")
        with ZipFile(staged) as archive:
            bad_file = archive.testzip()
            if bad_file:
                raise ValueError(f"压缩包校验失败：{bad_file}")
        # Exclusive creation also protects a concurrent export with the same filename.
        with destination.open("xb") as output, staged.open("rb") as source:
            import shutil
            shutil.copyfileobj(source, output)
    return destination


def main() -> int:
    from config import DATA_DIR
    parser = argparse.ArgumentParser(description="导出当前账号和学生数据到私有演示包；请先停止系统，避免导出期间数据变化")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    destination = args.output or ROOT.parent / "exports" / f"zhijiao-demo-{datetime.now():%Y%m%d-%H%M%S}.zip"
    print(f"正在导出当前演示系统（包含账号和学生数据）：{destination}", flush=True)
    print(f"导出完成并通过校验：{export_demo(ROOT, DATA_DIR, destination)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
