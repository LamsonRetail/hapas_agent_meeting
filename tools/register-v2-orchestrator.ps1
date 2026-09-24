param(
    [switch]$StartNow
)

$ErrorActionPreference = 'Stop'

$taskName = 'V2_Orchestrator'
$repoRoot = Split-Path -Parent $PSScriptRoot
$target = Join-Path $repoRoot 'run-v2-auto.bat'

if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
    throw "Khong tim thay launcher V2: $target"
}

# Task Scheduler, khong phai terminal goi script, phai la tien trinh cha cua
# wrapper. Neu spawn truc tiep tu mot PowerShell/Codex job tam thoi, Windows co
# the don ca cay process khi job ket thuc; do la nguyen nhan V2 tat ngay sau cac
# lan restart ngay 12/08/2026.
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$sid = $identity.User.Value
$action = New-ScheduledTaskAction `
    -Execute $env:ComSpec `
    -Argument ('/d /c ""{0}""' -f $target) `
    -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $sid
$principal = New-ScheduledTaskPrincipal `
    -UserId $sid `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -DontStopOnIdleEnd `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'MeetingxLark V2 orchestrator; Task Scheduler owns and restarts the wrapper.' `
    -Force | Out-Null

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
Write-Host "[+] Da dang ky $taskName (owner: $sid, state: $($task.State))."

# Cơ chế Startup cũ không có watchdog. Để cả hai cùng tồn tại còn tạo race lúc
# đăng nhập: hai wrapper có thể cùng vượt qua phép kiểm trước khi Python kịp lên.
# Đổi đuôi thay vì xóa để có thể phục hồi bằng tay nếu cần.
$startupItem = Join-Path $env:APPDATA `
    'Microsoft\Windows\Start Menu\Programs\Startup\V2_Orchestrator.vbs'
if (Test-Path -LiteralPath $startupItem -PathType Leaf) {
    $disabled = "$startupItem.disabled"
    if (Test-Path -LiteralPath $disabled) {
        $disabled = "$disabled.$(Get-Date -Format 'yyyyMMddHHmmss')"
    }
    Move-Item -LiteralPath $startupItem -Destination $disabled
    Write-Host "[+] Da vo hieu hoa Startup item cu: $disabled"
}

if ($StartNow) {
    Start-ScheduledTask -TaskName $taskName
    Write-Host "[+] Da yeu cau Task Scheduler bat $taskName."
}
