@echo off
REM Launcher cho CUA VAO bot hoi dap. Plugin Hermes `v2-enroll-gate` spawn file
REM NAY, khong import V2 truc tiep - Hermes chay Python 3.11 cua uv, V2 chay
REM Python 3.12 voi bo thu vien rieng (httpx/cryptography), nhap cheo la moi loi.
REM
REM Vao:  v2-gate.bat <union_id> [user_id] [ten] [chat_type] [chat_id] [mentions]
REM Ra :  MOT dong JSON o stdout, vi du
REM       {"decision":"allow","open_id":"ou_..."}
REM       {"decision":"invite","nonce":"...","link":"https://..."}
REM       {"decision":"wait","reason":"..."}
REM
REM chat_type: dm | group | channel | thread (SessionSource.chat_type cua
REM Hermes). Mac dinh CHI `dm` duoc tra loi - bo loc quyen cap cho NGUOI HOI,
REM khong cap cho NGUOI DOC, nen trong phong nhieu nguoi thi cau tra loi lot
REM sang nguoi khong co quyen xem. Bo trong = plugin doi cu -> V2 cho di tiep.
REM
REM chat_id: chat_id cua phong (oc_...). Chi co nghia voi phong NHOM: nhom phai
REM nam trong V2_GROUP_QA_CHATS moi duoc hoi, va chinh id nay buoc vao ve phien
REM de bot CHI tra loi ve cuoc hop ma nhom do duoc moi (xem qa.rooms_index).
REM
REM stdout CHI co JSON (log cua V2 di stderr) - cung ky luat voi mcp-meetings.bat.

set PY=C:\Users\PC\AppData\Local\Programs\Python\Python312\python.exe
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d "%~dp0"

"%PY%" -m v2 gate --union-id "%~1" --user-id "%~2" --name "%~3" --chat-type "%~4" --chat-id "%~5" --mentions "%~6"
