@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0ollama-model.ps1" %*
exit /b %ERRORLEVEL%
