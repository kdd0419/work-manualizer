@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo GitHub에 올릴 파일만 골라 묶는 중입니다... (.env, 녹화, 실행 결과, .venv 제외)
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\make_github_zip.ps1"
if errorlevel 1 (
  echo.
  echo 묶기에 실패했습니다. 위 메시지를 확인하세요.
) else (
  echo.
  echo 완료! dist 폴더를 엽니다.
  echo  - teamproject_github 폴더 안의 파일을 GitHub 저장소 "Add file - Upload files" 화면에 끌어다 놓으세요.
  echo  - zip 파일은 Google Drive, 카카오톡 등으로 팀원에게 보낼 때 사용하세요.
  start "" "dist"
)
echo.
pause
