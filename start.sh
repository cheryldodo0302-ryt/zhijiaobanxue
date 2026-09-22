#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"

MODE="${1:-all}"
case "$MODE" in
  all|api|worker|web-dev|web-build|test|ai-check) ;;
  *) echo "未知模式：$MODE" >&2; exit 2 ;;
esac

ensure_web() {
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    echo "未找到 Node.js/npm，请安装 Node.js 20 或更高版本。" >&2
    exit 2
  fi
  node -e "if(Number(process.versions.node.split('.')[0])<20)process.exit(1)" || {
    echo "Node.js 版本过低，请安装 Node.js 20 或更高版本。" >&2
    exit 2
  }
  if [ ! -d web/node_modules ]; then
    (cd web && npm ci --no-audit --no-fund)
  fi
}

if [ "$MODE" = "web-dev" ] || [ "$MODE" = "web-build" ]; then
  ensure_web
  cd web
  if [ "$MODE" = "web-dev" ]; then exec npm run dev; else exec npm run build; fi
fi

PYTHON_BIN=""
if [ -n "${ZHIJIAO_PYTHON:-}" ] && command -v "$ZHIJIAO_PYTHON" >/dev/null 2>&1; then
  PYTHON_BIN="$ZHIJIAO_PYTHON"
fi
if [ -z "$PYTHON_BIN" ]; then
  for candidate in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 \
      && "$candidate" -c 'import sys; assert sys.version_info[:2] in ((3,10),(3,11),(3,12))' >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi
if [ -z "$PYTHON_BIN" ]; then
  echo "未找到 Python 3.10、3.11 或 3.12；也可设置 ZHIJIAO_PYTHON。" >&2
  exit 2
fi

VENV_DIR="${ZHIJIAO_VENV_DIR:-.venv}"
if [ -d "$VENV_DIR/Scripts" ] && [ ! -x "$VENV_DIR/bin/python" ]; then
  VENV_DIR=".venv-posix"
fi
VENV_PYTHON="$VENV_DIR/bin/python"
if [ ! -x "$VENV_PYTHON" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
if ! "$VENV_PYTHON" -c 'import sys; assert sys.version_info[:2] in ((3,10),(3,11),(3,12))' >/dev/null 2>&1; then
  echo "项目虚拟环境不是 Python 3.10/3.11/3.12，请重命名 $VENV_DIR 后重试。" >&2
  exit 2
fi
if ! "$VENV_PYTHON" -c 'import fastapi,faiss,sqlalchemy,sklearn,pypdf,docx,pptx,openpyxl,requests,jwt,argon2' >/dev/null 2>&1; then
  "$VENV_PYTHON" -m pip install -r requirements.txt
fi

if [ "$MODE" = "all" ]; then ensure_web; fi
echo "使用项目 Python：$($VENV_PYTHON -c 'import platform; print(platform.python_version())')"
if [ "$MODE" = "all" ] || [ "$MODE" = "api" ]; then
  "$VENV_PYTHON" scripts/bootstrap_demo.py --if-empty
fi

case "$MODE" in
  all) exec "$VENV_PYTHON" scripts/run_all.py ;;
  api) exec "$VENV_PYTHON" -m uvicorn api:app --host 127.0.0.1 --port 8000 ;;
  worker) exec "$VENV_PYTHON" scripts/run_ingestion_worker.py ;;
  test) exec "$VENV_PYTHON" -m pytest -q ;;
  ai-check) exec "$VENV_PYTHON" qwen_check.py ;;
esac
