@echo off
call "%~dp0run-python.cmd" "%~dp0notify_attention.py" %*
exit /b %errorlevel%
