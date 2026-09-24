# Khoi dong lai `python -m v2 run --send` de nap code moi.
#
# BAT BUOC ket thuc voi DUNG MOT orchestrator. `v2 run` khong co khoa
# don-the-hien nao (orchestrator.py:292 chi la threading.Lock, chi chan trong
# CUNG mot tien trinh), nen hai ban chay song song = phat bien ban HAI LAN cho
# nguoi du. So tay repo ghi day la loi muc 1.
#
# Ban dau (04/08/2026) file nay chi giet python roi de wrapper cu tu bat lai.
# SAI: vong `:loop` trong run-v2-auto.bat KHONG kiem tra lai truoc khi bat -
# chot chong trung cua no chi chay MOT lan luc khoi dong. Ket qua do that:
# hai wrapper cung song, wrapper cu dang dem nguoc 120s de spawn ban thu hai.
# Nen gio: don HET (ca wrapper lan python), roi bat lai dung mot.
#
# Vi sao la FILE chu khong phai mot dong lenh dan vao terminal: logic nay can
# `$_.Name` va `$_.CommandLine`. Dan qua Git Bash thi bash go `$_` truoc khi
# PowerShell kip doc - lenh thanh `unsetenv.Name -eq 'python.exe'`, khong khop
# gi, khong co gi bi dung, va khong bao loi ro rang. Da gap that.
#
# Loc `Name -eq 'python.exe'` la BAT BUOC: dong lenh powershell cung chua chuoi
# '-m v2 run' nen thieu bo loc do thi no tu khop voi chinh minh.

[CmdletBinding()]
param(
    [switch]$ForceBusy
)

$ErrorActionPreference = 'Stop'
$taskName = 'V2_Orchestrator'
$registerTask = Join-Path $PSScriptRoot 'tools\register-v2-orchestrator.ps1'

function Get-Orchestrators {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq 'python.exe' -and $_.CommandLine -match '-m v2 run'
    })
}

function Get-Wrappers {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq 'cmd.exe' -and $_.CommandLine -match 'run-v2-auto\.bat'
    })
}

$procs = @(Get-Orchestrators)
$wraps = @(Get-Wrappers)

Write-Host "[*] Dang chay: $($procs.Count) orchestrator, $($wraps.Count) wrapper"
foreach ($p in $procs) { Write-Host "      python  PID $($p.ProcessId)  (tu $($p.CreationDate))" }
foreach ($w in $wraps) { Write-Host "      wrapper PID $($w.ProcessId)  (tu $($w.CreationDate))" }

if ($procs.Count -gt 1) {
    Write-Host ''
    Write-Host '[!] CO NHIEU HON MOT ORCHESTRATOR - moi bien ban dang bi phat lap.'
}

# Con file trong work/ tuc CO THE dang tai hoac phien am do. Dung giua chung thi
# job quay ve hang doi va lan sau phien am LAI tu dau (CPU, khong mat du lieu).
#
# 18/08/2026 - vi sao khong con DEM file nua: ban cu coi moi file trong work/ la
# "dang xu ly" roi tu huy. Do duoc: obsgd4544....mp4 238 MB dong tu 13/08 trong
# khi job do da `held` (xong). Nghia la MOI lan restart tu 13/08 den 18/08 deu bi
# chinh la chan nay huy - ban va nao cung khong vao duoc may, va khong ai biet.
# Nay hoi STATUS JOB qua `v2.bat workfiles` (pipeline.work_files): chi status
# queued/transcribing/recapping moi la dang dung that; con lai la rac, in ra roi
# di tiep. Python hong / khong doc duoc DB thi FAIL-CLOSED ve hoi nguoi.
$work = Join-Path $PSScriptRoot 'v2\data\work'
if (Test-Path $work) {
    $files = @(Get-ChildItem $work -File -ErrorAction SilentlyContinue)
    if ($files.Count -gt 0) {
        # KHONG dung `2>&1`: PowerShell 5.1 boc stderr cua native exe thanh
        # ErrorRecord, va voi $ErrorActionPreference='Stop' o dau file thi cu do
        # lam CHINH SCRIPT NAY chet truoc khi restart duoc gi. Chi doc stdout;
        # python hong thi stdout khong co dong '[workfiles]' -> fail-closed.
        $wf = @(& (Join-Path $PSScriptRoot 'v2.bat') workfiles)
        $busy  = @($wf | Where-Object { $_ -match '^BUSY' })
        $stale = @($wf | Where-Object { $_ -match '^STALE' })
        $ok    = @($wf | Where-Object { $_ -match '^\[workfiles\]' }).Count -gt 0

        if (-not $ok) {
            Write-Host ''
            Write-Host "[!] KHONG phan loai duoc $($files.Count) file trong v2\data\work:"
            $wf | ForEach-Object { Write-Host "      $_" }
            if ($ForceBusy) {
                Write-Host '[*] ForceBusy - van restart.'
            } else {
                $ans = Read-Host 'Khong biet co job nao dang chay. Van dung? (go: co)'
                if ($ans -ne 'co') { Write-Host '[*] Da huy, khong dung gi.'; exit 3 }
            }
        } else {
            if ($stale.Count -gt 0) {
                Write-Host ''
                Write-Host "[*] $($stale.Count) file RAC trong v2\data\work (job da xong/khong con) - khong chan restart:"
                $stale | ForEach-Object { Write-Host "      $_" }
                Write-Host '    Don duoc bang tay; de lai chi ton dia.'
            }
            if ($busy.Count -gt 0) {
                Write-Host ''
                Write-Host "[!] CANH BAO: $($busy.Count) cuoc hop DANG xu ly that:"
                $busy | ForEach-Object { Write-Host "      $_" }
                if ($ForceBusy) {
                    Write-Host '[*] ForceBusy da duoc chi dinh - se restart va job se tu thu lai.'
                } else {
                    $ans = Read-Host 'Van dung? (go: co)'
                    if ($ans -ne 'co') { Write-Host '[*] Da huy, khong dung gi.'; exit 3 }
                }
            }
        }
    }
}

# Dung task sau khi da qua xac nhan busy. Neu khong, huy o prompt se de task
# bi dung du nguoi van chon khong restart.
$scheduled = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($scheduled -and $scheduled.State -eq 'Running') {
    Write-Host "[*] Dung Scheduled Task $taskName"
    Stop-ScheduledTask -TaskName $taskName
    Start-Sleep -Seconds 2
}

# Wrapper TRUOC, python SAU. Nguoc lai thi wrapper kip thay con chet va bat
# lai ngay giua chung - dung cai da xay ra.
Write-Host ''
foreach ($w in $wraps) {
    Write-Host "[*] Dung wrapper PID $($w.ProcessId)"
    try { Stop-Process -Id $w.ProcessId -Force -ErrorAction Stop } catch {
        Write-Host "    (bo qua: $($_.Exception.Message))"
    }
}
foreach ($p in Get-Orchestrators) {
    Write-Host "[*] Dung orchestrator PID $($p.ProcessId)"
    try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {
        Write-Host "    (bo qua: $($_.Exception.Message))"
    }
}

Start-Sleep -Seconds 3

$still = @(Get-Orchestrators)
$stillW = @(Get-Wrappers)
if ($still.Count -gt 0 -or $stillW.Count -gt 0) {
    Write-Host ''
    Write-Host "[x] Van con $($still.Count) python + $($stillW.Count) wrapper - chua don sach."
    Write-Host '    Mo lai cua so nay bang quyen Administrator roi chay lai.'
    exit 1
}

Write-Host '[+] Da don sach.'
Write-Host '[*] Dang ky watchdog va bat lai dung MOT ban qua Task Scheduler...'
if (-not (Test-Path -LiteralPath $registerTask -PathType Leaf)) {
    Write-Host "[x] Thieu helper dang ky task: $registerTask"
    exit 1
}
try {
    & $registerTask -StartNow
} catch {
    Write-Host "[x] Khong dang ky/khoi dong duoc Scheduled Task: $($_.Exception.Message)"
    Write-Host '    Khong roi ve Start-Process: tien trinh con se lai chet theo terminal.'
    exit 1
}

# Doi that su thay tien trinh moi roi hay bao thanh cong. Bao "da bat" ma
# khong kiem la kieu bao cao da lam user tuong da restart trong khi chua.
Write-Host '[*] Doi orchestrator moi len...'
$new = @()
foreach ($i in 1..20) {
    Start-Sleep -Seconds 2
    $new = @(Get-Orchestrators)
    if ($new.Count -ge 1) { break }
}

Write-Host ''
if ($new.Count -eq 1) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $task -or $task.State -ne 'Running') {
        Write-Host "[x] Co orchestrator nhung task $taskName khong Running; khong co watchdog."
        exit 1
    }
    Write-Host "[+] XONG - dung 1 orchestrator: PID $($new[0].ProcessId); task Running"
} elseif ($new.Count -eq 0) {
    Write-Host '[x] Chua thay orchestrator moi sau 40 giay. Xem log ben duoi.'
    exit 1
} else {
    Write-Host "[x] Co $($new.Count) orchestrator - VAN BI TRUNG. Chay lai file nay."
    exit 1
}

Write-Host ''
Write-Host '    Kiem lai sau vai phut:  v2.bat doctor'
Write-Host "    Log: v2\data\logs\v2-$(Get-Date -Format 'yyyy-MM-dd').log"
exit 0
