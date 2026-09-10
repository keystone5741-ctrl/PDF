#!/usr/bin/env bash
# PDF 편집기 실행 (macOS / Linux).  사용법: ./run.sh [파일.pdf]
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "[오류] python3 를 찾을 수 없습니다. https://www.python.org/downloads/ 에서 파이썬을 설치하세요."
  exit 1
fi
if [ ! -f .venv/.ok ]; then
  echo "처음 실행입니다. 필요한 구성 요소를 설치합니다. 1~2분 정도 걸립니다..."
  python3 -m venv .venv || { echo "[오류] 가상환경을 만들지 못했습니다."; exit 1; }
  .venv/bin/python -m pip install --upgrade pip >/dev/null 2>&1
  .venv/bin/python -m pip install -r requirements.txt || { echo "[오류] 라이브러리 설치에 실패했습니다. 인터넷 연결을 확인하세요."; exit 1; }
  touch .venv/.ok
fi
echo "편집기를 시작합니다. 브라우저가 안 열리면 주소창에 127.0.0.1:8765 를 입력하세요. 종료: Ctrl+C"
exec .venv/bin/python main.py "$@"
