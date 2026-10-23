@echo off
call "%~dp0run-python.cmd" "%~dp0codex_usage_watch.py" %*
exit /b %errorlevel%
