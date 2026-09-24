$ErrorActionPreference = 'Stop'

$taskName = 'V2_ApplyUpdateWhenIdle'
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = 'C:\Users\PC\AppData\Local\Programs\Python\Python312\python.exe'
$restart = Join-Path $repoRoot 'restart-v2.ps1'
$dbPath = Join-Path $repoRoot 'v2\data\state.db'
$workDir = Join-Path $repoRoot 'v2\data\work'
$activeCountScript = Join-Path $PSScriptRoot 'v2-active-count.py'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Khong tim thay Python V2: $python"
}

$active = & $python $activeCountScript $dbPath
if ($LASTEXITCODE -ne 0 -or -not ($active -match '^\d+$')) {
    throw "Khong doc duoc active job count: $active"
}
$workFiles = @(Get-ChildItem -LiteralPath $workDir -File -ErrorAction SilentlyContinue)
if ([int]$active -gt 0 -or $workFiles.Count -gt 0) {
    Write-Host "[*] V2 con $active active job / $($workFiles.Count) work file; doi luot sau."
    exit 0
}

Write-Host '[*] Hang doi da rong; nap ban va bang restart an toan.'
& $restart
if ($LASTEXITCODE -ne 0) {
    throw "restart-v2.ps1 that bai: $LASTEXITCODE"
}

# Đây là task một-lần về mặt mục đích. Xóa chính nó sau khi đã xác nhận restart
# thành công để không tạo thêm một cơ chế nền tồn tại vĩnh viễn.
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
Write-Host "[+] Da ap dung va xoa task tam $taskName."
