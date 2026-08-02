@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Cai/go tu chay V2

REM ============================================================
REM  Dang ky V2 tu chay khi dang nhap Windows.
REM
REM  Uu tien Scheduled Task (/SC ONLOGON) vi no co the chay som va co
REM  the them restart-on-failure. Neu Windows doi quyen admin (rat hay
REM  gap) thi roi ve Startup folder - dung cach Hermes dang dung.
REM
REM  Cach chay an: dung .vbs de khong hien cua so cmd. Log van ghi vao
REM  v2\data\logs\v2-<ngay>.log nen khong mat gi.
REM ============================================================

set TASK=V2_Orchestrator
set TASK_ALERTS=V2_Alerts
set TARGET_ALERTS=%~dp0run-v2-alerts.bat
set TARGET=%~dp0run-v2-auto.bat
set VBS=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\V2_Orchestrator.vbs

if /i "%1"=="/go" goto uninstall
if /i "%1"=="/trangthai" goto status

echo.
echo   Se dang ky:  %TARGET%
echo.
echo   Luu y: may PHAI dang nhap Windows thi V2 moi chay (day la may ca
echo   nhan, khong phai server). Sleep / log out = ca ba phan dung.
echo.

REM --- Thu Scheduled Task truoc --------------------------------
schtasks /Create /TN "%TASK%" /TR "\"%TARGET%\"" /SC ONLOGON /RL LIMITED /F >nul 2>&1
if %errorlevel%==0 (
    echo  [+] Da tao Scheduled Task "%TASK%" ^(chay khi dang nhap^).
    call :alerts_task
    goto also_check
)

echo  [!] Khong tao duoc Scheduled Task ^(thuong la thieu quyen admin^).
echo      Roi ve Startup folder.

REM --- Canh bao chay NGOAI vong run ----------------------------
REM Xem :alerts_task o cuoi file. Goi ca o nhanh Scheduled Task lan nhanh
REM Startup folder: du V2 chay bang cach nao thi phep kiem "run da chet
REM chua" van phai co, va no la thu duy nhat con song khi run khong con.

REM --- Roi ve Startup folder -----------------------------------
> "%VBS%" echo Set s = CreateObject("WScript.Shell")
>> "%VBS%" echo s.Run """%TARGET%""", 0, False
if exist "%VBS%" (
    echo  [+] Da tao "%VBS%" ^(chay an khi dang nhap^).
    call :alerts_task
) else (
    echo  [x] Tao Startup item that bai. Kiem quyen ghi vao %APPDATA%.
    exit /b 1
)

:also_check
echo.
echo  Kiem lai:
call :status_body
echo.
echo  Bat NGAY bay gio ma khong cho dang nhap lai:
echo      start "" "%TARGET%"
echo.
echo  Go bo:   %~nx0 /go
exit /b 0

REM ------------------------------------------------------------
:uninstall
echo  Dang go...
schtasks /Delete /TN "%TASK%" /F >nul 2>&1
if %errorlevel%==0 (echo  [+] Da xoa Scheduled Task.) else (echo  [-] Khong co Scheduled Task.)
schtasks /Delete /TN "%TASK_ALERTS%" /F >nul 2>&1
if %errorlevel%==0 (echo  [+] Da xoa task canh bao.) else (echo  [-] Khong co task canh bao.)
if exist "%VBS%" (del "%VBS%" & echo  [+] Da xoa Startup item.) else (echo  [-] Khong co Startup item.)
echo.
echo  LUU Y: lenh nay chi go phan TU CHAY. Tien trinh dang chay thi
echo  van chay - dong cua so hoac ket thuc python -m v2.
exit /b 0

REM ------------------------------------------------------------
:status
call :status_body
exit /b 0

:status_body
schtasks /Query /TN "%TASK%" >nul 2>&1
if %errorlevel%==0 (echo    Scheduled Task : CO) else (echo    Scheduled Task : khong)
if exist "%VBS%" (echo    Startup item   : CO) else (echo    Startup item   : khong)
schtasks /Query /TN "%TASK_ALERTS%" >nul 2>&1
if %errorlevel%==0 (echo    Task canh bao  : CO) else (echo    Task canh bao  : KHONG ^(run chet se khong ai bao^))
REM findstr chu khong phai find: neu goi tu Git Bash thi `find` bi hieu thanh
REM Unix find va bao loi "/i: No such file or directory".
tasklist /FI "IMAGENAME eq python.exe" /FO CSV 2>nul | findstr /I "python.exe" >nul
if %errorlevel%==0 (echo    python dang chay: CO ^(chua chac la V2 - xem log^)) else (echo    python dang chay: khong)
exit /b 0

REM ------------------------------------------------------------
REM  Task canh bao: chay moi 15 phut, KHONG phu thuoc vong `run`.
REM
REM  /SC MINUTE /MO 15 chu khong phai ONLOGON: no phai chay ca khi vong
REM  `run` da chet giua chung, ma chuyen do khong sinh ra lan dang nhap
REM  nao ca. 15 phut la du nhanh - moc "run im qua lau" mac dinh la 30.
REM
REM  Khong dat duoc thi CHI canh bao, khong lam that bai ca lenh: V2 van
REM  chay binh thuong, chi la mat luoi an toan ngoai cung.
:alerts_task
schtasks /Create /TN "%TASK_ALERTS%" /TR "\"%TARGET_ALERTS%\"" /SC MINUTE /MO 15 /RL LIMITED /F >nul 2>&1
if %errorlevel%==0 (
    echo  [+] Da tao task "%TASK_ALERTS%" ^(moi 15 phut, canh bao khi `run` chet^).
) else (
    echo  [!] KHONG tao duoc task canh bao. `run` chet thi se khong ai duoc bao.
    echo      Tao tay: schtasks /Create /TN "%TASK_ALERTS%" /TR "\"%TARGET_ALERTS%\"" /SC MINUTE /MO 15 /F
)
exit /b 0
