@echo off
chcp 65001 >nul
cd /d "%~dp0"
title MyFans Web Launcher

where python >nul 2>&1
if errorlevel 1 goto python_missing

python web_launcher.py
set "launcher_exit=%errorlevel%"
if "%launcher_exit%"=="0" exit /b 0

echo.
echo Web launcher failed. Exit code: %launcher_exit%
pause
exit /b %launcher_exit%

:python_missing
echo Python was not found. Run SETUP.bat first.
echo.
pause
exit /b 1
