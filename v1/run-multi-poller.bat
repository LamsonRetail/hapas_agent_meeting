@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM ============================================================
REM  Central multi-user poller (Giai doan 2 - phu nhieu nhan su)
REM  Van dung BOT de gui. Doc minute bang token cua tung nguoi.
REM  LUU Y: file .bat khong duoc co dau tieng Viet.
REM ============================================================

set TRANSCRIPT_SOURCE=whisper
set WHISPER_URL=http://localhost:8000
set WHISPER_DIR=
set POLL_INTERVAL=300

REM --- Che do gui: --send gui that, --dry-run chi in ra
set SEND_MODE=--send

set LLM_BASE_URL=https://api.openai.com/v1
set LLM_MODEL=gpt-4o-mini

if exist config.bat (
    call config.bat
) else (
    echo  [!] Khong thay config.bat - se chay khong co recap.
)

REM ============================================================

if not exist users.json (
    echo  [X] Chua co users.json. Enroll nguoi dung truoc:
    echo         copy users.example.json users.json
    echo         python enroll_user.py ^<profile^>
    echo.
    pause
    exit /b 1
)

echo.
echo  Central multi-user poller
echo  ------------------------------------------------
echo  Nguon transcript : %TRANSCRIPT_SOURCE%
echo  Whisper server   : %WHISPER_URL%
echo  Quet moi         : %POLL_INTERVAL% giay
if "%SEND_MODE%"=="--dry-run" echo  Che do           : THU - khong gui that
if "%SEND_MODE%"=="--send" echo  Che do           : GUI THAT
echo  ------------------------------------------------
echo.

if "%SEND_MODE%"=="--send" (
    echo  CANH BAO: se gui tin nhan THAT cho nguoi duoc moi cua MOI
    echo            nhan su da enroll. Nhan Ctrl+C de huy.
    timeout /t 10
)

REM --- Chi can Whisper server khi nguon la whisper
if not "%TRANSCRIPT_SOURCE%"=="whisper" goto runpoller

curl -s -o nul -m 3 "%WHISPER_URL%/health"
if not errorlevel 1 (
    echo [server] Whisper da chay san.
    goto runpoller
)
if not defined WHISPER_DIR (
    echo [server] Whisper server CHUA CHAY. Bat run-server.bat truoc,
    echo          hoac dien WHISPER_DIR trong file nay.
    echo.
    pause
    exit /b 1
)
echo [server] Dang bat Whisper server tai %WHISPER_DIR% ...
start "Whisper Server" cmd /c "cd /d "%WHISPER_DIR%" ^&^& run-server.bat"
set /a tries=0
:waithealth
set /a tries+=1
if %tries% gtr 60 (
    echo [server] Cho qua 5 phut van chua san sang.
    pause
    exit /b 1
)
timeout /t 5 /nobreak >nul
curl -s -o nul -m 3 "%WHISPER_URL%/health"
if errorlevel 1 (
    echo [server] dang khoi dong... ^(%tries%/60^)
    goto waithealth
)
echo [server] Whisper san sang.
echo.

:runpoller
python multi_poller.py %SEND_MODE%

echo.
echo Poller da dung.
pause
