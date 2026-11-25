@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 업무 녹화 매크로 AI
if not exist ".venv\Scripts\python.exe" (
  echo 처음 실행이라면 setup.bat 을 먼저 더블클릭해 설치하세요.
  pause
  exit /b 1
)
echo ============================================
echo  업무 녹화 매크로 AI
echo ============================================
echo  잠시 후 브라우저에 관리 화면이 열립니다: http://localhost:8000
echo  이 검은 창을 닫으면 프로그램이 종료됩니다.
echo.
".venv\Scripts\python.exe" -m app.server
pause