@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0sync-now.ps1" %*
exit /b %ERRORLEVEL%
