@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_wealth_offline.ps1" %*
set "result=%errorlevel%"
echo.
if not "%result%"=="0" echo Installation did not complete. See the message above.
pause
exit /b %result%
