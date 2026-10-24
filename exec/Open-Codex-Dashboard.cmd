@echo off
setlocal
call "%~dp0..\codex-dashboard.cmd" start --open %*
if errorlevel 1 (
  echo.
  echo Could not open the Codex dashboard. Check the error above.
  pause
  exit /b 1
)
exit /b 0
