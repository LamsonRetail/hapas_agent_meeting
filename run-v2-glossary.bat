@echo off
chcp 65001 >nul
cd /d "%~dp0"
title V2 glossary digest - mot luot

REM ============================================================
REM  Chay MOT luot `python -m v2 glossary-digest` roi thoat. Danh cho
REM  Scheduled Task chay HANG TUAN: DM admin danh sach thuat ngu ung vien
REM  (gap >= V2_GLOSSARY_MIN_COUNT cuoc) de duyet qua bot.
REM
REM  Tao Scheduled Task tuan (vi du sang thu Hai 9h). DA TAO 03/08/2026 - lenh
REM  duoi day la de tao lai tren may khac. Chay trong PowerShell:
REM    $tr = '"D:\MeetingxLark\run-v2-glossary.bat"'
REM    schtasks /Create /TN "V2_GlossaryDigest" /TR $tr /SC WEEKLY /D MON /ST 09:00 /F
REM  Duong dan phai GHI THANG: `%~dp0` chi no ra trong file .bat, chep sang shell
REM  la chuoi tho. Va phai boc dau nhay qua bien $tr - cmd/PowerShell an mat lop
REM  nhay trong "\"...\"" roi schtasks bao 'Invalid argument' (da dinh 03/08).
REM
REM  Xem truoc ma khong DM ai:  python -m v2 glossary-digest --dry-run
REM
REM  Chay chong len vong `run` la binh thuong: chi DOC bang glossary +
REM  gui DM, khong dung gi trong pipeline.
REM ============================================================

set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
set PY=C:\Users\PC\AppData\Local\Programs\Python\Python312\python.exe

set LOGDIR=%~dp0v2\data\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
REM Ten log theo ngay. KHONG cat %DATE% ra ma dung: dinh dang cua no theo
REM Regional Settings, va tren chinh may nay 03/08/2026 no doi tu "03/08/2026"
REM sang "Mon 08/03/2026" -> cong thuc cu de ra "glossary-03-08-Mon.log".
REM Hau qua khong phai xau ma la kho doc: nguoi ta mo v2\data\logs tim theo
REM ngay, thay ten la thi tuong khong co log. Hoi PowerShell mot cau la xong.
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%d
set LOG=%LOGDIR%\glossary-%TODAY%.log

echo [%DATE% %TIME%] --- v2 glossary-digest >> "%LOG%"
"%PY%" -m v2 glossary-digest >> "%LOG%" 2>&1
if errorlevel 1 echo [%DATE% %TIME%] [x] v2 glossary-digest thoat voi loi >> "%LOG%"
exit /b 0
