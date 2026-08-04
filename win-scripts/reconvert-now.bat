@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0reconvert-now.ps1" %*
exit /b %ERRORLEVEL%
