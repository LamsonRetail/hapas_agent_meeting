@echo off
REM ============================================================
REM  MAU CAU HINH - copy file nay thanh config.bat roi dien gia tri that.
REM  config.bat da nam trong .gitignore nen KHONG bi day len git.
REM
REM  Luu y:
REM    - KHONG co dau cach quanh dau bang
REM    - KHONG dat dau nhay quanh gia tri
REM    - Dung:  set OPENAI_API_KEY=sk-abc123
REM    - Sai:   set OPENAI_API_KEY = "sk-abc123"
REM ============================================================

REM --- Key OpenAI de sinh recap (de trong -> chi gui transcript, khong recap)
set OPENAI_API_KEY=

REM --- Thu muc chua run-server.bat cua Whisper (de trong neu tu bat tay)
set WHISPER_DIR=

REM --- App "Meeting Agent CDS" - dung cho CA event listener LAN gui tin nhan.
REM     Dien ca hai dong duoi thi he thong gui bang app rieng thay vi
REM     muon app cua anh Thien qua lark-cli. De TRONG ca hai -> quay ve lark-cli.
set EVENT_APP_SECRET=

set SENDER_APP_ID=cli_aae288361ef89eed
set SENDER_APP_SECRET=
set LARK_PROFILE=mine
