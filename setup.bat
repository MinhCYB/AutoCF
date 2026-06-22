@echo off
echo ============================================
echo   Polygon Uploader - Setup
echo ============================================
echo.

:: Check Python version
python --version 2>nul
if errorlevel 1 (
    echo [ERROR] Python khong duoc tim thay. Vui long cai dat Python 3.10+
    pause
    exit /b 1
)

:: Install dependencies
echo [1/2] Cai dat dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Cai dat dependencies that bai!
    pause
    exit /b 1
)

:: Copy .env if not exists
echo [2/2] Kiem tra file .env...
if not exist .env (
    copy .env.example .env
    echo [INFO] Da tao file .env tu .env.example
    echo [INFO] Vui long mo file .env va dien API keys cua ban
) else (
    echo [INFO] File .env da ton tai
)

echo.
echo ============================================
echo   Setup hoan tat! Chay "run.bat" de bat dau
echo ============================================
pause
