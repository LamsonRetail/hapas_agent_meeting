@echo off
REM Launcher cho MCP server du lieu hop — Hermes spawn file NAY, khong spawn
REM "python -m v2" truc tiep.
REM
REM Vi sao can launcher:
REM   1. "python -m v2" chi chay duoc khi cwd = E:\meetingxlark. Truong `cwd`
REM      trong mcp_servers cua Hermes KHONG xac nhan duoc la co ho tro, nen dung
REM      launcher tu cd cho chac.
REM   2. Hermes bundle Python 3.11 rieng cua no. Neu de "python" thi co the tro
REM      vao Python cua Hermes — noi KHONG co httpx/cryptography cua V2. Nen ghi
REM      duong dan tuyet doi tai day.
REM   3. UTF-8: ten cuoc hop co tieng Viet, console cp1252 se crash.
REM
REM Doi Python? Sua dong PY duoi day. Kiem tra bang:
REM   echo {"jsonrpc":"2.0","id":1,"method":"tools/list"} | mcp-meetings.bat

set PY=C:\Users\PC\AppData\Local\Programs\Python\Python312\python.exe
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d "%~dp0"

REM KHONG echo gi ra stdout: stdout la kenh giao thuc JSON-RPC.
"%PY%" -m v2 mcp
