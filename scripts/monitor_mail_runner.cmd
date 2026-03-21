@echo off
setlocal
cd /d %~dp0..
start "Mail Runner Monitor" powershell -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File "%~dp0monitor_mail_runner_controller.ps1" %*
exit /b 0
