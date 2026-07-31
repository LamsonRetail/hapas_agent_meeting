@echo off
REM Launcher cho CUA VAO bot hoi dap. Plugin Hermes `v2-enroll-gate` spawn file
REM NAY, khong import V2 truc tiep — Hermes chay Python 3.11 cua uv, V2 chay
REM Python 3.12 voi bo thu vien rieng (httpx/cryptography), nhap cheo la moi loi.
REM
REM Vao:  v2-gate.bat <union_id> [user_id] [ten]
REM Ra :  MOT dong JSON o stdout, vi du
REM       {"decision":"allow","open_id":"ou_..."}
REM       {"decision":"invite","nonce":"...","link":"https://..."}
REM       {"decision":"wait","reason":"..."}
REM
REM stdout CHI co JSON (log cua V2 di stderr) — cung ky luat voi mcp-meetings.bat.

set PY=C:\Users\Hi WINDOWS 11\AppData\Local\Programs\Python\Python312\python.exe
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d "%~dp0"

"%PY%" -m v2 gate --union-id "%~1" --user-id "%~2" --name "%~3"
