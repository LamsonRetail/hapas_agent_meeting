@echo off
cd /d "%~dp0"
title Hermes watchdog

REM ============================================================
REM  Canh gateway Hermes: CHET hoac DO thi bat lai.
REM
REM  Vi sao can (do 31/07/2026): Scheduled Task `Hermes_Gateway` co
REM  RestartOnFailure 999 lan / 1 phut, NHUNG no chay qua mot .vbs spawn
REM  TACH ROI roi thoat ngay -> task ve trang thai Ready trong vai giay va
REM  Task Scheduler KHONG so huu tien trinh gateway. Restart-on-failure vi
REM  the khong bao gio ap dung: gateway chet la nam chet toi lan dang nhap
REM  Windows ke tiep, khong ai biet.
REM
REM  Phep do: HTTP GET /health cua api_server (cong 8642). No tra
REM  {"status":"ok"}, KHONG can API key, va KHONG sinh dong log nao trong
REM  gateway.log (da do: /v1/models thi sinh mot dong WARNING moi lan).
REM  Manh hon kiem tien trinh con song: no chung minh vong lap HTTP con
REM  phuc vu duoc, tuc bat duoc ca truong hop DO chu khong chi chet han.
REM
REM  Chi bat lai sau %FAILS% lan hong LIEN TIEP: mot cu timeout le, hay luc
REM  gateway dang khoi dong, khong duoc phep giet mot phien agent dang chay.
REM
REM  Bat lai bang `hermes gateway restart` chu KHONG Stop-Process theo chuoi:
REM  so tay muc 16 bay 4 - kill theo chuoi rong da giet lay worker con hai lan.
REM
REM  LUAT: file .bat chi dung ASCII + CRLF (so tay muc 16 bay 1). Khong `chcp`.
REM ============================================================

set "HERMES=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\hermes.exe"
set "HEALTH=http://127.0.0.1:8642/health"
set "LOGDIR=%LOCALAPPDATA%\hermes\logs"
set "INTERVAL=60"
set "FAILS=3"

if not exist "%LOGDIR%" mkdir "%LOGDIR%"
for /f "tokens=1-3 delims=/-. " %%a in ("%DATE%") do set TODAY=%%c-%%b-%%a
set "LOG=%LOGDIR%\watchdog-%TODAY%.log"

if not exist "%HERMES%" (
    call :log "[x] khong thay %HERMES% - DUNG"
    exit /b 1
)

REM Chot chong chay trung: giu doc quyen mot file khoa suot vong doi khoi
REM lenh. Ban thu hai khong mo duoc handle do -> di vao nhanh ||.
REM Dung cach nay thay vi di tim tien trinh theo chuoi, vi mot .bat di tim
REM 'hermes-watchdog' se TU KHOP voi chinh no (so tay muc 16 bay 3).
2>nul (
    9>"%LOGDIR%\watchdog.lock" (
        call :main
    )
) || (
    call :log "[!] da co mot watchdog dang chay - thoat"
    exit /b 0
)
exit /b 0

:main
setlocal enabledelayedexpansion
call :log "=========== watchdog bat dau (do moi %INTERVAL%s, bat lai sau %FAILS% lan hong lien tiep) ==========="
set /a bad=0
set /a restarts=0
set "WAIT=%INTERVAL%"

:loop
curl -s -o nul --max-time 10 "%HEALTH%"
if errorlevel 1 (
    set /a bad+=1
    REM Dung "[-]" chu KHONG "[!]": trong khoi enabledelayedexpansion, dau `!`
    REM nam TRONG chuoi bi cmd nuot lam dau mo/dong bien. Da do 31/07/2026:
    REM dong "[!] /health khong tra loi (!bad!/3)" in ra thanh "[bad/3)".
    call :log "[-] /health khong tra loi (!bad!/%FAILS%)"
    if !bad! geq %FAILS% (
        set /a restarts+=1
        call :log "[*] bat lai gateway lan !restarts!: hermes gateway restart"
        "%HERMES%" gateway restart >> "%LOG%" 2>&1
        call :log "[*] da goi restart, cho 45s roi do lai"
        REM `ping` chu khong `timeout`: xem ghi chu o cuoi vong lap.
        ping -n 46 127.0.0.1 >nul
        set /a bad=0
        REM Bat lai nhieu lan ma khong bao gio len = van de khac, khong phai
        REM gateway do. Hay gap nhat: api_server tat vi thieu API_SERVER_KEY
        REM trong .env cua Hermes -> /health khong bao gio tra loi. Cham lai
        REM de khoi quay vong dot, va noi to ly do trong log.
        if !restarts! geq 5 (
            set "WAIT=600"
            call :log "[x] da bat lai 5 lan ma /health van im. Kiem API_SERVER_KEY trong %LOCALAPPDATA%\hermes\.env - thieu no thi api_server KHONG bat, va recap cua V2 cung chet theo. Cham lai con 600s/lan."
        )
    )
) else (
    if !bad! gtr 0 call :log "[+] /health tra loi lai binh thuong"
    if !restarts! gtr 0 call :log "[+] gateway da song lai sau !restarts! lan bat"
    set /a bad=0
    set /a restarts=0
    set "WAIT=%INTERVAL%"
)
REM PHAI dung `ping`, KHONG dung `timeout` (do 02/08/2026): `timeout` tu chet
REM ngay khi stdin khong phai console - dung canh watchdog chay nen. Do that:
REM `timeout /t 5` mat 0,098s va in "ERROR: Input redirection is not
REM supported"; `ping -n 6` mat 5,14s.
REM O DAY hau qua nang hon o run-v2-auto: mat cai tre nay thi ca vong lap chay
REM nhanh het muc curl cho phep, tuc cu 3 lan do hong la goi
REM `hermes gateway restart` MOT lan - bat lai gateway lien tuc thay vi
REM 45s/lan, va lop cham-lai-600s sau 5 lan cung vo tac dung.
REM ping can N+1 goi de cho N giay, nen cong 1 truoc.
set /a WAITP=!WAIT!+1
ping -n !WAITP! 127.0.0.1 >nul
goto loop

:log
echo [%DATE% %TIME%] %~1
echo [%DATE% %TIME%] %~1 >> "%LOG%"
exit /b 0
