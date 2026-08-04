@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0verify-ollama-gpu.ps1" %*
exit /b %ERRORLEVEL%
