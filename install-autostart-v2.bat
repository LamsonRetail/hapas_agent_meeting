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
    goto also_check
)

echo  [!] Khong tao duoc Scheduled Task ^(thuong la thieu quyen admin^).
echo      Roi ve Startup folder.

REM --- Roi ve Startup folder -----------------------------------
> "%VBS%" echo Set s = CreateObject("WScript.Shell")
>> "%VBS%" echo s.Run """%TARGET%""", 0, False
if exist "%VBS%" (
    echo  [+] Da tao "%VBS%" ^(chay an khi dang nhap^).
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
REM findstr chu khong phai find: neu goi tu Git Bash thi `find` bi hieu thanh
REM Unix find va bao loi "/i: No such file or directory".
tasklist /FI "IMAGENAME eq python.exe" /FO CSV 2>nul | findstr /I "python.exe" >nul
if %errorlevel%==0 (echo    python dang chay: CO ^(chua chac la V2 - xem log^)) else (echo    python dang chay: khong)
exit /b 0
