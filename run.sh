#!/usr/bin/env bash
# PDF 편집기 실행 (macOS / Linux).  사용법: ./run.sh [파일.pdf]
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
fi
exec .venv/bin/python main.py "$@"
