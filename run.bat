@echo off
rem PDF 편집기 실행 (Windows).  사용법: run.bat [파일.pdf]
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv && .venv\Scripts\pip install -q -r requirements.txt
)
.venv\Scripts\python main.py %*
