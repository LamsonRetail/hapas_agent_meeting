@echo off
REM Chep plugin `v2-enroll-gate` tu repo sang cho Hermes doc, roi bat no.
REM
REM Vi sao can: Hermes chi nap plugin tu %LOCALAPPDATA%\hermes\plugins\, cho do
REM nam NGOAI repo nen khong ai version-control. Ban trong repo la ban GOC;
REM chay file nay de dong bo sang ban dang chay.
REM
REM Sau khi chay: PHAI kiem `hermes plugins list` thay `enabled`. Neu khong,
REM cua vao khong co ai giu ma FEISHU_ALLOW_ALL_USERS=true => bot mo cho ca
REM tenant (so tay §14).

setlocal
set SRC=%~dp0v2-enroll-gate
set DST=%LOCALAPPDATA%\hermes\plugins\v2-enroll-gate
set HERMES=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\hermes.exe

if not exist "%SRC%\plugin.yaml" (
    echo  [x] Khong thay %SRC%\plugin.yaml
    exit /b 1
)

echo  Chep %SRC%  ->  %DST%
if not exist "%DST%" mkdir "%DST%"
copy /Y "%SRC%\plugin.yaml" "%DST%\" >nul
copy /Y "%SRC%\__init__.py" "%DST%\" >nul

if not exist "%HERMES%" (
    echo  [!] Khong thay hermes.exe — chep xong nhung chua bat duoc.
    echo      Tu chay: hermes plugins enable v2-enroll-gate
    exit /b 0
)

echo  Bat plugin...
"%HERMES%" plugins enable v2-enroll-gate
echo.
echo  Kiem lai (phai thay "enabled"):
"%HERMES%" plugins list | findstr v2-enroll-gate
echo.
echo  Roi khoi dong lai gateway:  hermes gateway restart
endlocal
