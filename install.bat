@echo off
chcp 65001 >nul
title Install QQ Bot

echo ============================================
echo   QQ Bot - First Time Setup
echo ============================================
echo.

echo [1/3] Checking Python...
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python not found. Install Python 3.10+ first.
    echo Download: https://www.python.org/downloads/
    pause
    exit /b
)
python --version

echo.
echo [2/3] Installing Python packages...
pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Package install failed
    pause
    exit /b
)
echo Done.

echo.
echo [3/3] Creating .env template...
cd /d %~dp0
if not exist config\.env (
    python -c "
with open('config/.env', 'w') as f:
    f.write('''# ===== REQUIRED =====
# DeepSeek LLM API key
# Get from: https://platform.deepseek.com/api_keys
LLM_API_KEY_HASH=YOUR_DEEPSEEK_KEY_HERE

# ===== OPTIONAL =====
# GPT Image 2 key (for image generation)
GPT_IMAGE2_API_KEY_HASH=YOUR_GPTIMAGE2_KEY_HERE
''')
    print('Created config/.env')
" ) else (
    echo config/.env already exists, skipped.
)

echo.
echo ============================================
echo   Setup complete!
echo.
echo   Next:
echo   1. Edit config\.env - fill your API keys
echo   2. Make sure QQ NT is installed
echo   3. Run start.bat
echo ============================================
pause
