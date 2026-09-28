@echo off
cd /d "%~dp0"
title V2 Dashboard

REM ============================================================
REM  V3 YC4: web dashboard bien ban hop (dang nhap bang Lark).
REM    - nghe 127.0.0.1:%DASHBOARD_PORT% (mac dinh 8765)
REM    - Cloudflare Tunnel tro meeting.lamsonretail.com va
REM      meeting.hapas-ai.tech vao cong nay (xem docs/V3_SPECS.md YC4)
REM    - can DASHBOARD_SECRET trong v2\.env
REM  Tien trinh RIENG voi "v2 run": dashboard chet khong keo theo pipeline.
REM ============================================================

:loop
python -m v2 dashboard
echo [!] Dashboard dung - bat lai sau 10 giay (Ctrl+C de thoat han).
timeout /t 10 >nul
goto loop
