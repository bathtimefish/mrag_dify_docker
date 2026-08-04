@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0docling-doctor.ps1" %*
exit /b %ERRORLEVEL%
