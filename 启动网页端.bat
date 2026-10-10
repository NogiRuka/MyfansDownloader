@echo off
chcp 65001 >nul
cd /d "%~dp0"
title MyFans 下载器网页端

where python >nul 2>&1
if errorlevel 1 (
    echo 未检测到 Python，请先双击运行 SETUP.bat。
    echo.
    pause
    exit /b 1
)

python web_launcher.py
if errorlevel 1 (
    echo.
    echo 网页端未能正常启动，请查看上方错误信息。
    pause
)
