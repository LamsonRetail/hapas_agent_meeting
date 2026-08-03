@echo off
chcp 65001 >nul
cd /d "%~dp0.."
REM Heartbeat V2 - chay moi 5 phut qua Task Scheduler (V2_Heartbeat).
REM Ghi 1 dong -> v2\data\logs\heartbeat-<ngay>.log. Chi ASCII + CRLF.
set PY=C:\Users\PC\AppData\Local\Programs\Python\Python312\python.exe
set PYTHONIOENCODING=utf-8
"%PY%" tools\heartbeat.py
