@echo off
chcp 65001 >nul
cd /d "%~dp0"
title V2 Orchestrator - launcher

REM ============================================================
REM  Bat ca he thong V2 bang 1 cu nhap:
REM    1. Whisper server (cua so rieng) neu chua chay
REM    2. Cho server san sang
REM    3. python -m v2 doctor (kham suc khoe)
REM    4. python -m v2 run  (dry-run mac dinh)
REM
REM  Gui THAT: sua dong RUN o cuoi thanh:  python -m v2 run --send
REM ============================================================

set WHISPER_BAT=E:\whisper\run-server.bat
set HEALTH=http://localhost:8000/health

echo [*] Kiem tra whisper server...
curl -s -o nul %HEALTH%
if %errorlevel%==0 (
    echo [+] Whisper server da song.
) else (
    echo [*] Chua thay - dang bat "%WHISPER_BAT%" o cua so rieng...
    if not exist "%WHISPER_BAT%" (
        echo [x] Khong thay %WHISPER_BAT% - sua bien WHISPER_BAT trong file nay.
        pause
        exit /b 1
    )
    start "Whisper Server" cmd /c "%WHISPER_BAT%"
)

echo [*] Cho whisper san sang (lan dau tai model co the mat vai phut)...
set /a tries=0
:waitloop
curl -s -o nul %HEALTH%
if %errorlevel%==0 goto ready
set /a tries+=1
if %tries% geq 120 (
    echo [x] Cho qua lau - kiem tra cua so Whisper Server.
    pause
    exit /b 1
)
timeout /t 3 >nul
goto waitloop

:ready
echo [+] Whisper san sang.
echo.
echo === Kham suc khoe ===
python -m v2 doctor
echo.

echo === Bat orchestrator (dry-run) ===
echo     Ctrl+C de dung. Gui that: sua file thanh "python -m v2 run --send".
echo.
python -m v2 run

pause
