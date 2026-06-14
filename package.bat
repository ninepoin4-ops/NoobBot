@echo off
chcp 65001 >nul
title Package QQ Bot

echo ============================================
echo   Package Noob Bot
echo ============================================
echo.

cd /d %~dp0
python package.py

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Packaging failed
    pause
    exit /b
)

echo.
pause
