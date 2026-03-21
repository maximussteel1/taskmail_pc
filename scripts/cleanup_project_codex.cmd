@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0cleanup_project_codex.ps1" %*
