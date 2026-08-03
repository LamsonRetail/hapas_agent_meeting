@echo off
REM ============================================================
REM  Gom SAU thu KHONG di duong git vao mot goi mang sang may moi.
REM
REM  Dung:  tools\goi-may-moi.bat [thu-muc-dich]
REM         mac dinh dich = D:\may-moi
REM
REM  Chay duoc NHIEU LAN. Lan cuoi phai chay SAU KHI da tat vong `run`
REM  o may cu - moi enroll / job phat sinh giua luc chep va luc tat se
REM  mat IM LANG neu khong lam vay (docs\MAY_MOI.md muc 2 buoc 0).
REM
REM  KHONG dua goi nay len git / OneDrive / thu muc dong bo: no chua
REM  LARK_APP_SECRET, V2_FERNET_KEY, token Lark cua moi nguoi da enroll,
REM  va nguyen van noi dung hop.
REM
REM  Vi sao khong chep thang v2\data\state.db: DB chay WAL, file .db la
REM  ban THIEU nhung van mo duoc binh thuong nen khong ai biet (do
REM  03/08/2026: .db 172 KB, -wal 4,0 MB). Nen script goi `v2 backup`
REM  (VACUUM INTO) roi lay ban moi nhat.
REM
REM  LUAT: file .bat chi dung ASCII + CRLF (so tay muc 16 bay 1).
REM ============================================================

setlocal enabledelayedexpansion
cd /d "%~dp0.."

set "DEST=%~1"
if "%DEST%"=="" set "DEST=D:\may-moi"
if "%WHISPER_DIR%"=="" set "WHISPER_DIR=E:\whisper"
set "KEYFILE=%USERPROFILE%\.meetingxlark\fernet-key.txt"
set /a MISSING=0

echo.
echo   Nguon : %CD%
echo   Dich  : %DEST%
echo.

REM --- 1. Ban sao NHAT QUAN cua state.db ------------------------
echo [*] Tao ban sao state.db (VACUUM INTO)...
python -m v2 backup
if errorlevel 1 (
    echo  [x] `v2 backup` that bai - DUNG. Khong co ban sao thi dung mang gi ca.
    exit /b 1
)

REM Lay file state-*.db MOI NHAT theo thoi gian sua.
set "SNAP="
for /f "delims=" %%f in ('dir /b /o-d "v2\data\backups\state-*.db" 2^>nul') do (
    if not defined SNAP set "SNAP=v2\data\backups\%%f"
)
if not defined SNAP (
    echo  [x] Khong thay file state-*.db nao trong v2\data\backups - DUNG.
    exit /b 1
)
echo  [+] Ban sao: !SNAP!

REM --- 2. Dung khung thu muc ------------------------------------
for %%d in ("%DEST%" "%DEST%\v2" "%DEST%\v2\data" "%DEST%\hermes" "%DEST%\fernet-key") do (
    if not exist %%d mkdir %%d >nul 2>&1
)

REM --- 3. Chep tung thu ------------------------------------------
REM state.db: doi ten LUON tai day. May moi chi viec chep de len
REM v2\data\state.db, khong phai nho doi ten - do la buoc de quen nhat.
call :copy1 "!SNAP!"                          "%DEST%\v2\data\state.db"
call :copy1 "v2\.env"                         "%DEST%\v2\.env"
call :copy1 "%KEYFILE%"                       "%DEST%\fernet-key\fernet-key.txt"
call :copy1 "%LOCALAPPDATA%\hermes\.env"      "%DEST%\hermes\.env"
call :copy1 "%LOCALAPPDATA%\hermes\config.yaml" "%DEST%\hermes\config.yaml"

REM transcripts: khong mang = job cu mat kha nang "phat lai khong phien
REM am lai", tuc phai chay lai buoc DAT NHAT cua ca he thong.
echo [*] Chep v2\data\transcripts\ ...
robocopy "v2\data\transcripts" "%DEST%\v2\data\transcripts" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (echo  [x] transcripts: robocopy loi & set /a MISSING+=1) else (echo  [+] transcripts)

REM whisper: bo .venv (dung lai o may moi, va may moi co GPU nen bo thu
REM vien khac han) va hop.mp3 (8 MB file thu tu thang 7).
if exist "%WHISPER_DIR%" (
    echo [*] Chep %WHISPER_DIR% ...
    robocopy "%WHISPER_DIR%" "%DEST%\whisper" /E /XD ".venv" "__pycache__" /XF "hop.mp3" /NFL /NDL /NJH /NJS /NP >nul
    if errorlevel 8 (echo  [x] whisper: robocopy loi & set /a MISSING+=1) else (echo  [+] whisper ^(bo .venv, hop.mp3^))
) else (
    echo  [x] Khong thay %WHISPER_DIR% - dat bien WHISPER_DIR neu no o cho khac.
    set /a MISSING+=1
)

REM --- 4. Bang SHA256 de doi chieu o may moi ---------------------
echo [*] Tinh SHA256...
powershell -NoProfile -Command "Push-Location '%DEST%'; Get-ChildItem -Recurse -File | Where-Object { $_.Name -ne 'SHA256.txt' -and $_.Name -ne 'README.txt' } | Get-FileHash -Algorithm SHA256 | ForEach-Object { $_.Hash + '  ' + (Resolve-Path -Relative $_.Path) } | Set-Content -Encoding utf8 'SHA256.txt'; Pop-Location" >nul 2>&1
if exist "%DEST%\SHA256.txt" (echo  [+] SHA256.txt) else (echo  [!] Khong tinh duoc SHA256 & set /a MISSING+=1)

REM --- 5. README kem van tay khoa --------------------------------
> "%DEST%\README.txt" echo Goi chuyen may meetingxlark - tao %DATE% %TIME%
>> "%DEST%\README.txt" echo Nguon: %COMPUTERNAME%  %CD%
>> "%DEST%\README.txt" echo.
>> "%DEST%\README.txt" echo GOI NAY CHUA SECRET DANG TRAN. Khong dua len git/OneDrive/thu muc dong bo.
>> "%DEST%\README.txt" echo.
>> "%DEST%\README.txt" echo Dat vao dau o may moi:
>> "%DEST%\README.txt" echo   v2\.env                 -^> ^<repo^>\v2\.env
>> "%DEST%\README.txt" echo   v2\data\state.db        -^> ^<repo^>\v2\data\state.db  ^(xoa -wal, -shm neu con^)
>> "%DEST%\README.txt" echo   v2\data\transcripts\    -^> ^<repo^>\v2\data\transcripts\
>> "%DEST%\README.txt" echo   fernet-key\fernet-key.txt -^> %%USERPROFILE%%\.meetingxlark\fernet-key.txt
>> "%DEST%\README.txt" echo   hermes\.env, config.yaml  -^> %%LOCALAPPDATA%%\hermes\
>> "%DEST%\README.txt" echo   whisper\                -^> E:\whisper\  ^(dung lai .venv, xem MAY_MOI buoc 2^)
>> "%DEST%\README.txt" echo.
>> "%DEST%\README.txt" echo state.db o day la ban VACUUM INTO ^(nhat quan^), khong phai ban copy tho.
>> "%DEST%\README.txt" echo No chi dung den thoi diem ghi o tren - chay lai script SAU KHI tat
>> "%DEST%\README.txt" echo vong `run` o may cu de lay ban cuoi cung.
>> "%DEST%\README.txt" echo.
>> "%DEST%\README.txt" echo V2_FERNET_KEY va state.db phai di CUNG NHAU. Van tay khoa:
python -m v2 backup --list >> "%DEST%\README.txt" 2>&1
>> "%DEST%\README.txt" echo.
>> "%DEST%\README.txt" echo Quy trinh day du: docs\MAY_MOI.md
echo  [+] README.txt

REM --- 6. Tong ket ------------------------------------------------
echo.
if !MISSING! gtr 0 (
    echo  [!] XONG nhung THIEU !MISSING! muc - doc lai o tren truoc khi mang di.
) else (
    echo  [+] Du ca sau muc.
)
echo.
echo  Buoc tiep: chep "%DEST%" sang USB / may moi, roi doi chieu bang
echo             SHA256.txt o dau ben kia.
echo.
exit /b 0

REM ------------------------------------------------------------
:copy1
if not exist %1 (
    echo  [x] Khong thay %~1
    set /a MISSING+=1
    exit /b 0
)
copy /y %1 %2 >nul
if errorlevel 1 (
    echo  [x] Chep that bai: %~1
    set /a MISSING+=1
) else (
    echo  [+] %~nx1
)
exit /b 0
