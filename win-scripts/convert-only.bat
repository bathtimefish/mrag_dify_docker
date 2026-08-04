@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0convert-only.ps1" %*
exit /b %ERRORLEVEL%
