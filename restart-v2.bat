@echo off
chcp 65001 >nul
cd /d "%~dp0"
title V2 - khoi dong lai orchestrator

REM ============================================================
REM  Khoi dong lai `python -m v2 run --send` de nap code moi.
REM
REM  Chi la vo boc goi restart-v2.ps1 - toan bo logic nam trong file .ps1
REM  do, va nam trong FILE chu khong phai mot dong lenh dan qua terminal.
REM  Ly do day du viet o dau file .ps1: `$_` bi shell trung gian an mat.
REM
REM  Dung: bam doi vao file nay, hoac go  restart-v2.bat
REM ============================================================

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart-v2.ps1"

echo.
pause
