@echo off
chcp 65001 >nul
cd /d "%~dp0.."
title MeetingxLark - Heartbeat Monitor
set PY=C:\Users\PC\AppData\Local\Programs\Python\Python312\python.exe
set PYTHONIOENCODING=utf-8
"%PY%" tools\heartbeat_monitor.py %1
