@echo off
chcp 65001 >nul
cd /d "%~dp0"
title V2 Orchestrator - tu dong (dung Ctrl+C de dung)

REM ============================================================
REM  Ban KHONG TUONG TAC cua start-v2.bat - danh cho Scheduled Task /
REM  Startup folder. Khac biet co y thuc:
REM    * KHONG co `pause` o dau ca: chay nen ma pause la treo vinh vien.
REM    * `--send` (GUI THAT), khong phai dry-run.
REM    * Ghi log ra file, vi chay nen thi khong ai thay man hinh.
REM    * Tu bat lai neu tien trinh chet (mang hong, DB khoa, ...).
REM
REM  Bat/tat che do tu chay: install-autostart-v2.bat
REM  Muon chay tay co man hinh thi dung start-v2.bat.
REM ============================================================

REM PYTHONUNBUFFERED: BAT BUOC khi chuyen huong stdout vao file. Thieu no thi
REM output bi block-buffer -> log rong hang chuc phut, tuong la treo (da gap).
REM PYTHONIOENCODING: ten cuoc hop co tieng Viet, console cp1252 se crash.
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8

set WHISPER_BAT=E:\whisper\run-server.bat
set HEALTH=http://localhost:8000/health
set LOGDIR=%~dp0v2\data\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM Ten log theo ngay: v2-2026-07-31.log
for /f "tokens=1-3 delims=/-. " %%a in ("%DATE%") do set TODAY=%%c-%%b-%%a
set LOG=%LOGDIR%\v2-%TODAY%.log

call :log "================ khoi dong run-v2-auto ================"

REM --- Chot chong chay TRUNG ------------------------------------
REM Hai orchestrator cung luc = hai lan phat (so tay muc 1). Bam hai lan vao file
REM nay, hoac Startup item chay khi da co ban chay tay, la dung vao day.
REM Da tu gap khi test 31/07: 2 tien trinh `python -m v2 run --send` song song.
REM Loc theo Name='python.exe' la BAT BUOC: chinh dong lenh powershell nay cung
REM chua chuoi '-m v2 run', nen neu khong loc thi no TU KHOP voi chinh no va
REM lan nao cung bao "da co ban dang chay" (da gap 31/07, mat 10 phut).
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match '-m v2 run' }; if ($p) { exit 1 } else { exit 0 }" >nul 2>&1
if errorlevel 1 (
    call :log "[!] DA CO mot ban `v2 run` dang chay - thoat, khong bat ban thu hai"
    exit /b 0
)

REM --- Whisper -------------------------------------------------
curl -s -o nul %HEALTH%
if %errorlevel%==0 (
    call :log "[+] whisper da song"
) else (
    if not exist "%WHISPER_BAT%" (
        call :log "[x] khong thay %WHISPER_BAT% - DUNG. Sua bien WHISPER_BAT."
        exit /b 1
    )
    call :log "[*] bat whisper: %WHISPER_BAT%"
    start "Whisper Server" /min cmd /c "%WHISPER_BAT%"
)

REM Cho toi 6 phut (lan dau nap model cham). Khong san sang thi VAN chay tiep:
REM job se loi o buoc phien am va thu lai vong sau, thay vi khong chay gi ca.
set /a tries=0
:waitloop
curl -s -o nul %HEALTH%
if %errorlevel%==0 goto ready
set /a tries+=1
if %tries% geq 120 (
    call :log "[!] cho whisper qua 6 phut - van chay orchestrator (job se thu lai)"
    goto ready
)
timeout /t 3 >nul
goto waitloop

:ready
call :log "[+] whisper san sang"

REM --- Vong chinh: chet thi bat lai -----------------------------
REM Tre 120s giua hai lan de khong quay vong dot khi cau hinh sai.
:loop
call :log "[*] python -m v2 run --send"
python -m v2 run --send >> "%LOG%" 2>&1
call :log "[!] orchestrator thoat (ma %errorlevel%) - bat lai sau 120s"
timeout /t 120 >nul
goto loop

:log
echo [%DATE% %TIME%] %~1
echo [%DATE% %TIME%] %~1 >> "%LOG%"
exit /b 0
