@echo off
chcp 65001 >nul
cd /d "%~dp0"
title V2 canh bao - mot luot

REM ============================================================
REM  Chay MOT luot `python -m v2 alerts` roi thoat. Danh cho Scheduled
REM  Task chay dinh ky (mac dinh 15 phut) - xem install-autostart-v2.bat.
REM
REM  Vi sao phai chay TU NGOAI vong `run`, khong phai them mot muc nua
REM  vao trong no: `alerts.check_all()` von da duoc goi tu ben trong
REM  `orchestrator.run`, nen `run` chet la moi canh bao chet theo. Day
REM  la duong DUY NHAT con song khi tien trinh chinh khong con.
REM
REM  Phep kiem "vong run da im bao lau" (alerts._check_run_stale) chi co
REM  nghia o day. Goi tu trong `run` thi nhip song luon tuoi nen no
REM  khong bao gio keu - dung nhu vay.
REM
REM  Chay chong len vong `run` la BINH THUONG: moc chong spam nam o bang
REM  alert_state trong state.db (WAL + busy_timeout), khong phai o RAM,
REM  nen hai tien trinh khong sinh DM trung.
REM ============================================================

set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
set PY=C:\Users\PC\AppData\Local\Programs\Python\Python312\python.exe

set LOGDIR=%~dp0v2\data\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
REM Ten log theo ngay. KHONG cat %DATE% ra ma dung: dinh dang cua no theo
REM Regional Settings, va tren chinh may nay 03/08/2026 no doi tu "03/08/2026"
REM sang "Mon 08/03/2026" -> cong thuc cu de ra "alerts-03-08-Mon.log" (con
REM nguyen trong v2\data\logs). Do la kieu hong am tham: file van duoc ghi,
REM chi la nguoi tim log theo ngay khong thay no.
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%d
set LOG=%LOGDIR%\alerts-%TODAY%.log

REM Log RIENG, khong ghi chung v2-<ngay>.log: log cua vong `run` la thu
REM nguoi ta doc de lan lai mot cuoc hop, tron them mot dong moi 15 phut
REM vao do lam no kho doc. Va khi `run` chet thi day la file duy nhat con
REM moi - nhin ngay tao file la biet canh bao con chay hay khong.
echo [%DATE% %TIME%] --- v2 alerts >> "%LOG%"
"%PY%" -m v2 alerts >> "%LOG%" 2>&1
if errorlevel 1 echo [%DATE% %TIME%] [x] v2 alerts thoat voi loi >> "%LOG%"
exit /b 0
