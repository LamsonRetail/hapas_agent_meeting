@echo off
cd /d "%~dp0"

REM ============================================================
REM  Lark event listener - nghe event qua persistent connection
REM  Chay SONG SONG voi run-meeting-note.bat
REM ============================================================

REM --- App "Meeting Agent CDS" (app moi, dung rieng cho event)
set EVENT_APP_ID=cli_aae288361ef89eed
set EVENT_APP_SECRET=

REM --- Cau hinh dung chung voi poller
set TRANSCRIPT_SOURCE=whisper
set WHISPER_URL=http://localhost:8000
set DRY_RUN=1
set DELIVER_TO=ou_1bc55b6d5b20ee06cbee1326d5b72715
set OPENAI_API_KEY=
set LLM_BASE_URL=https://api.openai.com/v1
set LLM_MODEL=gpt-4o-mini

REM ============================================================

if not defined EVENT_APP_SECRET (
    echo.
    echo  [LOI] Chua dien EVENT_APP_SECRET.
    echo        Lay trong Console: Credentials ^& Basic Info -^> App Secret
    echo.
    pause
    exit /b 1
)

python event_listener.py

echo.
echo Listener da dung.
pause
