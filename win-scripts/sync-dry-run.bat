@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0sync-dry-run.ps1" %*
exit /b %ERRORLEVEL%
