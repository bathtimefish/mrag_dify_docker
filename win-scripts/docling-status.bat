@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0docling-status.ps1" %*
exit /b %ERRORLEVEL%
