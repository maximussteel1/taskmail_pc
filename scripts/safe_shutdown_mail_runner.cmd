@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0safe_shutdown_mail_runner.ps1" %*
