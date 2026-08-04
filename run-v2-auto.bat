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
set PY=C:\Users\PC\AppData\Local\Programs\Python\Python312\python.exe

set WHISPER_BAT=D:\whisper\run-server.bat
set HEALTH=http://localhost:8000/health
set LOGDIR=%~dp0v2\data\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM Ten log theo ngay: v2-2026-07-31.log. Tinh LAI moi vong (xem :setlog).
call :setlog

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
REM `ping` chu khong `timeout` - xem ly do o vong chinh ben duoi. Voi cho nay
REM hau qua rieng: `timeout` chet ngay nghia la 120 lan lap chay het trong vai
REM giay, tuc "cho toi 6 phut cho whisper nap model" thuc te la khong cho.
ping -n 4 127.0.0.1 >nul
goto waitloop

:ready
call :log "[+] whisper san sang"

REM --- Vong chinh: chet thi bat lai -----------------------------
REM Tre 120s giua hai lan de khong quay vong dot khi cau hinh sai.
REM
REM PHAI dung `ping`, KHONG dung `timeout` (do 02/08/2026): `timeout` tu chet
REM ngay khi stdin khong phai console - dung canh file .bat nay chay tu Task
REM Scheduler / Startup. Do that: `timeout /t 5` mat 0,098s va in
REM "ERROR: Input redirection is not supported"; `ping -n 6` mat 5,14s.
REM Hau qua that trong log 13:36:55 hom do: may dang logoff (ma thoat
REM -1073741205 = STATUS_DLL_INIT_FAILED_LOGOFF) va vong nay bat lai 17 lan
REM trong 0,93 giay - tuc cai tre 120s ghi o tren CHUA BAO GIO ton tai, va lop
REM chong "quay vong dot khi cau hinh sai" cung vay.
:loop
REM Tinh lai ten file log TRUOC moi lan chay: wrapper nay song qua nhieu ngay,
REM ma truoc 03/08/2026 `LOG` chi duoc tinh MOT lan luc khoi dong. Hau qua: log
REM cua ngay 03/08 nam trong file v2-2026-08-02.log. Khong mat du lieu, nhung
REM lan sau ai do chan loi "hom 5/8 co gi" se mo v2-2026-08-05.log, thay file
REM TRONG, roi ket luan he thong khong chay - dung kieu doc sai nguy hiem nhat.
call :setlog
call :log "[*] python -m v2 run --send"
"%PY%" -m v2 run --send >> "%LOG%" 2>&1
call :log "[!] orchestrator thoat (ma %errorlevel%) - bat lai sau 120s"
ping -n 121 127.0.0.1 >nul
goto loop

:log
echo [%DATE% %TIME%] %~1
echo [%DATE% %TIME%] %~1 >> "%LOG%"
exit /b 0

REM GIOI HAN CON LAI, phai biet: chuyen huong `>> "%LOG%"` duoc chot luc PHONG
REM tien trinh python, nen mot tien trinh song qua nua dem van ghi tiep vao file
REM cua ngay hom truoc cho toi khi no chet va vong lap phong lai. Thuc te may nay
REM khoi dong lai vai lan moi ngay (ngu / loi 31.2) nen sai lech chi con vai
REM tieng thay vi vai ngay. Muon dung tuyet doi thi phai cho chinh Python tu mo
REM file log theo ngay, khong dung chuyen huong cua cmd nua.
:setlog
REM KHONG cat %DATE% ra ma dung (sua 03/08/2026): dinh dang cua no theo Regional
REM Settings. Tren chinh may nay %DATE% doi tu "03/08/2026" sang "Mon 08/03/2026"
REM trong ngay, va cong thuc cu de ra ten "v2-03-08-Mon.log" - dung kieu hong am
REM tham nhat: log VAN duoc ghi day du, chi la nguoi lan loi mot cuoc hop mo
REM v2\data\logs tim "v2-2026-08-03.log" thi khong thay, roi ket luan he thong
REM khong chay. (File v2-03-08-Mon.log va alerts-03-08-Mon.log la di tich cua
REM dung loi do - giu lai, do la log that.)
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%d
set LOG=%LOGDIR%\v2-%TODAY%.log
exit /b 0
