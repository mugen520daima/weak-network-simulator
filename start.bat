@echo off
chcp 65001 >nul 2>&1
echo ========================================
echo   Weak Network Simulator - Launcher
echo ========================================
echo.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Requesting admin privileges...
    powershell -Command "Start-Process cmd -ArgumentList '/c cd /d \"%~dp0\" && \"%~f0\"' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [X] Python not found. Please install Python 3.8+
    pause
    exit /b 1
)

echo [*] Installing dependencies...
pip install flask pydivert -q

echo.
echo [*] Starting server...
echo [*] Open browser: http://127.0.0.1:5000
echo.
start http://127.0.0.1:5000
python app.py

pause
