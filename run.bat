@echo off
rem PDF editor launcher (Windows).  Usage: run.bat [file.pdf]
chcp 65001 >nul
cd /d "%~dp0"
title PDF 편집기
echo ============================================
echo   PDF 편집기
echo   이 창을 닫으면 프로그램이 종료됩니다.
echo ============================================
echo.

rem --- 파이썬 찾기 (py 런처 우선, 그 다음 python)
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if defined PY goto :found
python --version >nul 2>&1 && set "PY=python"
if defined PY goto :found

echo [오류] 파이썬을 찾을 수 없습니다.
echo.
echo   1. https://www.python.org/downloads/ 에서 파이썬을 설치하세요.
echo   2. 설치 첫 화면 맨 아래 "Add python.exe to PATH" 를 꼭 체크하세요.
echo   3. 이미 설치했다면 체크를 빠뜨린 것이니, 설치 파일을 다시 실행해
echo      "Modify" 를 고르고 "Add Python to environment variables" 를 켜세요.
echo.
goto :fail

:found
for /f "tokens=*" %%v in ('%PY% --version 2^>^&1') do echo 사용할 파이썬: %%v

rem --- 처음 한 번만: 가상환경 만들고 필요한 라이브러리 설치
if exist ".venv\.ok" goto :run
echo.
echo 처음 실행입니다. 필요한 구성 요소를 설치합니다. 1~2분 정도 걸립니다...
echo.
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [오류] 가상환경을 만들지 못했습니다.
    goto :fail
  )
)
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [오류] 라이브러리 설치에 실패했습니다. 인터넷 연결을 확인한 뒤 다시 실행하세요.
  goto :fail
)
echo ok> ".venv\.ok"
echo.
echo 설치가 끝났습니다.

:run
echo.
echo 편집기를 시작합니다. 잠시 후 브라우저가 열립니다.
echo 안 열리면 브라우저 주소창에  127.0.0.1:8765  를 입력하세요.
echo 종료하려면 이 창을 닫으세요.
echo.
".venv\Scripts\python.exe" main.py %*
if errorlevel 1 (
  echo.
  echo [오류] 프로그램이 오류로 종료되었습니다. 위 메시지를 확인하세요.
  goto :fail
)
goto :end

:fail
echo.
echo 아무 키나 누르면 창이 닫힙니다.
pause >nul
exit /b 1

:end
echo.
echo 프로그램이 종료되었습니다. 아무 키나 누르면 창이 닫힙니다.
pause >nul
exit /b 0
