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

$ErrorActionPreference = 'Stop'

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

# Con file trong work/ tuc dang tai hoac phien am do. Dung giua chung thi job
# quay ve hang doi va lan sau phien am LAI tu dau (CPU, khong mat du lieu).
$work = Join-Path $PSScriptRoot 'v2\data\work'
if (Test-Path $work) {
    $busy = @(Get-ChildItem $work -File -ErrorAction SilentlyContinue)
    if ($busy.Count -gt 0) {
        Write-Host ''
        Write-Host "[!] CANH BAO: $($busy.Count) file trong v2\data\work - co cuoc hop dang xu ly do:"
        $busy | ForEach-Object { Write-Host "      $($_.Name)" }
        $ans = Read-Host 'Van dung? (go: co)'
        if ($ans -ne 'co') { Write-Host '[*] Da huy, khong dung gi.'; exit 3 }
    }
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
Write-Host '[*] Bat lai dung MOT ban...'
Start-Process -FilePath (Join-Path $PSScriptRoot 'run-v2-auto.bat') -WindowStyle Minimized

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
    Write-Host "[+] XONG - dung 1 orchestrator: PID $($new[0].ProcessId)"
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
