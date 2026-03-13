@echo off
setlocal
cd /d %~dp0..
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0manage_mail_runner.ps1" start %*
