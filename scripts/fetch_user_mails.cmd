@echo off
setlocal
cd /d %~dp0..
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fetch_user_mails.ps1" %*
exit /b %ERRORLEVEL%
