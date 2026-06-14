@echo off
chcp 936 >nul
title Noob Bot

echo ============================================
echo   Noob Bot  启动器 / Launcher
echo.
echo   直接回车 = 扫码登录 / Scan QR code
echo   输入QQ号 = 快速登录 / Quick login
echo ============================================
echo.

set /p QQ_NUM=请输入QQ号后回车（直接回车则扫码）/ Enter QQ (or Enter for QR):

echo.
echo [1/2] 启动 NapCat / Starting NapCat...
echo.

set NAPCAT_DIR=%~dp0napcat\napcat

if "%QQ_NUM%"=="" (
    echo    模式 / Mode: 扫码登录 / QR code login
    echo    即将打开 NapCat 窗口，请用手机扫码
    echo    A NapCat window will open, scan with your phone
    echo.
    start "NapCat" cmd /k "chcp 936 >nul && cd /d %NAPCAT_DIR% && launcher-user.bat"
) else (
    echo    模式 / Mode: 快速登录 / Quick login ^(QQ=%QQ_NUM%^)
    echo    即将打开 NapCat 窗口 / A NapCat window will open
    echo.
    start "NapCat" cmd /k "chcp 936 >nul && cd /d %NAPCAT_DIR% && launcher-user.bat %QQ_NUM%"
)

echo    NapCat 已在新窗口启动 / NapCat started in a new window
echo    请在新窗口完成登录，完成后回到本窗口按任意键继续
echo    Complete login in the new window, then come back here and press any key
echo.
pause

echo.
echo [2/2] 启动 Python Bot / Starting Python Bot...
echo.
echo    启动后请在浏览器打开 WebUI / After startup, open WebUI in browser:
echo      http://127.0.0.1:8081
echo.
cd /d %~dp0
python main.py

if errorlevel 1 (
    echo.
    echo    [错误] Bot 异常退出，请检查日志 / Bot exited abnormally, check logs
    pause
)
