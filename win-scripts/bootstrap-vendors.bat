@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap-vendors.ps1" %*
exit /b %ERRORLEVEL%
