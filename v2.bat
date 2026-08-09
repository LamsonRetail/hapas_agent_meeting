@echo off
chcp 65001 >nul
REM ============================================================
REM  Launcher CHUNG cho moi lenh V2 - chay duoc tu BAT KY thu muc nao.
REM
REM  Vi sao can (04/08/2026): `python -m v2 ...` chi chay khi cwd =
REM  D:\MeetingxLark VA `python` tro dung Python 3.12 rieng cua V2. Dung o
REM  C:\Windows\system32 thi `python` trung venv cua Hermes (3.11, khong co
REM  httpx/cryptography cua V2) va bao 'No module named v2' - cau bao loi
REM  khong he goi y hai nguyen nhan that.
REM
REM  Dung:  D:\MeetingxLark\v2.bat <lenh> [tham so...]
REM  Vi du: v2.bat unauth --union-id on_X --yes
REM         v2.bat glossary-digest --dry-run
REM         v2.bat base-sync
REM         v2.bat doctor
REM ============================================================

set PY=C:\Users\PC\AppData\Local\Programs\Python\Python312\python.exe
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d "%~dp0"

if not exist "%PY%" (
    echo [x] Khong thay Python cua V2: %PY%
    echo     Sua bien PY trong file nay.
    exit /b 1
)

"%PY%" -m v2 %*
