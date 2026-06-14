@echo off
chcp 65001 >nul
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
echo [1/3] 配置 NapCat WebSocket / Configuring NapCat WebSocket...
echo.

cd /d %~dp0

REM 首次/改号都需要生成 NapCat 的 OneBot11 正向 WS 配置，否则 Bot 连不上
if "%QQ_NUM%"=="" (
    REM 扫码模式：QQ 号登录后才知道，用 --non-interactive 会失败；
    REM 这里只在有 QQ 号时自动配置。扫码用户首次需手动跑一次
    REM `python setup_napcat.py --qq <你的QQ号>`，或登录后补做。
    echo    扫码模式：登录后请在 WebUI 群聊页确认连接，或单独跑：
    echo      python setup_napcat.py --qq ^<你的QQ号^>
) else (
    python setup_napcat.py --qq %QQ_NUM% --non-interactive
    if errorlevel 1 (
        echo.
        echo    [警告] NapCat 配置失败，Bot 可能连不上。请检查上方提示。
        echo    可以稍后手动重跑：python setup_napcat.py
        echo.
    )
)

echo.
echo [2/3] 启动 NapCat / Starting NapCat...
echo.

set NAPCAT_DIR=%~dp0napcat\napcat

if "%QQ_NUM%"=="" (
    echo    模式 / Mode: 扫码登录 / QR code login
    echo    即将打开 NapCat 窗口，请用手机扫码
    echo    A NapCat window will open, scan with your phone
    echo.
    start "NapCat" cmd /k "chcp 65001 >nul && cd /d %NAPCAT_DIR% && launcher-user.bat"
) else (
    echo    模式 / Mode: 快速登录 / Quick login ^(QQ=%QQ_NUM%^)
    echo    即将打开 NapCat 窗口 / A NapCat window will open
    echo.
    start "NapCat" cmd /k "chcp 65001 >nul && cd /d %NAPCAT_DIR% && launcher-user.bat %QQ_NUM%"
)

echo    NapCat 已在新窗口启动 / NapCat started in a new window
echo    请在新窗口完成登录，完成后回到本窗口按任意键继续
echo    Complete login in the new window, then come back here and press any key
echo.
pause

echo.
echo [3/3] 启动 Python Bot / Starting Python Bot...
echo.
echo    启动后请在浏览器打开 WebUI / After startup, open WebUI in browser:
echo      http://127.0.0.1:8081
echo.
cd /d %~dp0
python main.py

if errorlevel 1 (
    echo.
    echo    [错误] Bot 异常退出，请检查日志 / Bot exited abnormally, check logs
    echo    提示：若卡在「连接 NapCat」，确认 napcat 窗口已完成登录
    pause
)
