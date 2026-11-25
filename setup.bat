@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 업무 녹화 매크로 AI - 설치
echo ============================================
echo  업무 녹화 매크로 AI - 처음 한 번만 실행하는 설치
echo ============================================
echo.

if exist ".venv\Scripts\python.exe" goto install

echo [1/4] Python 3.11 이상을 찾아 가상환경을 만드는 중...
python -c "import sys; assert sys.version_info >= (3, 11)" >nul 2>nul && (python -m venv .venv & goto install)
py -3 -c "import sys; assert sys.version_info >= (3, 11)" >nul 2>nul && (py -3 -m venv .venv & goto install)
if exist "%USERPROFILE%\anaconda3\python.exe" ("%USERPROFILE%\anaconda3\python.exe" -m venv .venv & goto install)
if exist "%USERPROFILE%\miniconda3\python.exe" ("%USERPROFILE%\miniconda3\python.exe" -m venv .venv & goto install)
echo.
echo [오류] Python 3.11 이상을 찾지 못했습니다.
echo        https://www.python.org/downloads/ 에서 설치하세요.
echo        설치 화면에서 "Add python.exe to PATH"를 꼭 체크한 뒤 이 파일을 다시 실행하세요.
pause
exit /b 1

:install
if not exist ".venv\Scripts\python.exe" goto fail
echo [2/4] 필요한 패키지 설치 중... (몇 분 걸릴 수 있습니다)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto fail
echo [3/4] 자동 실행용 브라우저(Chromium) 설치 중...
".venv\Scripts\python.exe" -m playwright install chromium || goto fail
echo [4/4] 설정 파일과 가상 데이터 준비 중...
if not exist ".env" copy ".env.example" ".env" >nul
".venv\Scripts\python.exe" -m mock_system.seed || goto fail
echo.
echo 설치가 끝났습니다. 이제 start.bat 을 더블클릭하세요.
echo (AI 기능을 쓰려면 .env 파일에 Gemini API 키를 넣으세요. 없어도 규칙 기반으로 동작합니다.)
pause
exit /b 0

:fail
echo.
echo [오류] 설치 중 문제가 생겼습니다. 위 메시지를 확인하세요.
pause
exit /b 1