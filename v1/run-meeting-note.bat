@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM ============================================================
REM  Luong tu dong meeting note
REM  LUU Y: file .bat khong duoc co dau tieng Viet.
REM ============================================================

REM --- Nguon transcript: whisper (qua server) hoac lark (co san)
set TRANSCRIPT_SOURCE=whisper

REM --- Dia chi Whisper server
set WHISPER_URL=http://localhost:8000
REM set WHISPER_API_KEY=

REM --- Thu muc chua run-server.bat cua Whisper.
REM     Dien vao thi file nay tu bat server luon.
set WHISPER_DIR=

REM --- Bao nhieu giay quet mot lan
set POLL_INTERVAL=300

REM --- Che do gui. Doi thanh --dry-run neu chi muon in ra man hinh.
REM     Truyen bang tham so dong lenh cho chac chan.
set SEND_MODE=--send

REM --- Ep gui cho 1 nguoi. Dang TAT -> gui cho toan bo nguoi duoc moi.
REM     Bo REM o dong duoi neu muon chi gui cho minh.
REM set DELIVER_TO=ou_1bc55b6d5b20ee06cbee1326d5b72715

set LLM_BASE_URL=https://api.openai.com/v1
set LLM_MODEL=gpt-4o-mini

REM --- Nap cau hinh rieng (key, duong dan). File config.bat khong bi
REM     ghi de khi cap nhat code, nen key dien mot lan la xong.
if exist config.bat (
    call config.bat
) else (
    echo  [!] Khong thay config.bat - se chay khong co recap.
)

REM ============================================================

echo.
echo  Luong tu dong meeting note
echo  ------------------------------------------------
echo  Nguon transcript : %TRANSCRIPT_SOURCE%
echo  Whisper server   : %WHISPER_URL%
echo  Quet moi         : %POLL_INTERVAL% giay
if defined OPENAI_API_KEY echo  Recap            : %LLM_MODEL%
if not defined OPENAI_API_KEY echo  Recap            : TAT - dien OPENAI_API_KEY trong config.bat
if "%SEND_MODE%"=="--dry-run" echo  Che do           : THU - khong gui that
if "%SEND_MODE%"=="--send" echo  Che do           : GUI THAT
if defined DELIVER_TO echo  Nguoi nhan       : chi %DELIVER_TO%
if not defined DELIVER_TO echo  Nguoi nhan       : TOAN BO nguoi duoc moi
echo  ------------------------------------------------
echo.

if "%SEND_MODE%"=="--send" (
    if not defined DELIVER_TO (
        echo  CANH BAO: se gui tin nhan THAT cho TAT CA nguoi duoc moi
        echo            vao cuoc hop. Nhan Ctrl+C de huy, hoac
        timeout /t 10
    )
)

REM --- Chi can Whisper server khi nguon la whisper
if not "%TRANSCRIPT_SOURCE%"=="whisper" goto runpoller

curl -s -o nul -m 3 "%WHISPER_URL%/health"
if not errorlevel 1 (
    echo [server] Whisper da chay san.
    goto runpoller
)

if not defined WHISPER_DIR (
    echo [server] Whisper server CHUA CHAY.
    echo          Bat run-server.bat truoc, hoac dien WHISPER_DIR trong file nay.
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
    echo.
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
python meeting_poller.py %SEND_MODE%

echo.
echo Poller da dung.
pause
