@echo off
chcp 65001 >nul
cd /d "%~dp0"
title V2 glossary digest - mot luot

REM ============================================================
REM  Chay MOT luot `python -m v2 glossary-digest` roi thoat. Danh cho
REM  Scheduled Task chay HANG TUAN: DM admin danh sach thuat ngu ung vien
REM  (gap >= V2_GLOSSARY_MIN_COUNT cuoc) de duyet qua bot.
REM
REM  Tao Scheduled Task tuan (vi du sang thu Hai 9h), chay Administrator:
REM    schtasks /Create /TN "V2_GlossaryDigest" /TR "\"%~dp0run-v2-glossary.bat\"" ^
REM             /SC WEEKLY /D MON /ST 09:00 /F
REM
REM  Chay chong len vong `run` la binh thuong: chi DOC bang glossary +
REM  gui DM, khong dung gi trong pipeline.
REM ============================================================

set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
set PY=C:\Users\PC\AppData\Local\Programs\Python\Python312\python.exe

set LOGDIR=%~dp0v2\data\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
for /f "tokens=1-3 delims=/-. " %%a in ("%DATE%") do set TODAY=%%c-%%b-%%a
set LOG=%LOGDIR%\glossary-%TODAY%.log

echo [%DATE% %TIME%] --- v2 glossary-digest >> "%LOG%"
"%PY%" -m v2 glossary-digest >> "%LOG%" 2>&1
if errorlevel 1 echo [%DATE% %TIME%] [x] v2 glossary-digest thoat voi loi >> "%LOG%"
exit /b 0
