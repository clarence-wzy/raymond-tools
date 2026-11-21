#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3，请先安装 Python 3.10+：https://www.python.org/downloads/"
  read -r -p "按回车退出..."
  exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "当前 Python 版本过低，需要 3.10 或更高。"
  read -r -p "按回车退出..."
  exit 1
fi

exec python3 app.py "$@"
