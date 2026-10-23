@echo off
setlocal
if defined CODEX_ATTENTION_PYTHON (
  "%CODEX_ATTENTION_PYTHON%" %*
  exit /b
)
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
  "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" %*
  exit /b
)
where py.exe >nul 2>nul
if not errorlevel 1 (
  py -3 %*
  exit /b
)
where python.exe >nul 2>nul
if not errorlevel 1 (
  python %*
  exit /b
)
echo Python 3.10 or newer is required. Set CODEX_ATTENTION_PYTHON to its executable path. 1>&2
exit /b 1
