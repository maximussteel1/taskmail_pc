@echo off
setlocal
cd /d %~dp0..
start "Mail Runner Monitor" powershell -NoExit -NoProfile -ExecutionPolicy Bypass -File "%~dp0monitor_mail_runner.ps1" %*
exit /b 0
