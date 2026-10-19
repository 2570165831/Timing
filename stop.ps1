param([switch]$RequireIdle)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$timingPidFile = Join-Path $PSScriptRoot ".runtime\server.pid"
$timingPython = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".venv\Scripts\python.exe"))

function Get-TimingService {
    $candidates = @()
    if (Test-Path -LiteralPath $timingPidFile) { $candidates += [int](Get-Content -LiteralPath $timingPidFile) }
    try { $candidates += @((Get-NetTCPConnection -LocalPort 7861 -State Listen -ErrorAction SilentlyContinue).OwningProcess) } catch {}
    foreach ($candidate in ($candidates | Where-Object { $_ } | Select-Object -Unique)) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$candidate" -ErrorAction SilentlyContinue
        if (-not $process) { continue }
        if ($process.Name -notlike "python*") { continue }
        if ($process.CommandLine -notmatch "-m timing --serve") { continue }
        return $process
    }
    return $null
}

$timingProcess = Get-TimingService
if (-not $timingProcess) {
    if (Test-Path -LiteralPath $timingPidFile) { Remove-Item -LiteralPath $timingPidFile }
    Write-Host "没有记录中的 Timing 服务。"
    exit
}
if ($RequireIdle) {
    $timingState = Invoke-RestMethod "http://127.0.0.1:7861/api/status" -TimeoutSec 5
    if ($timingState.app -ne "Timing" -or $timingState.queued -gt 0) { throw "Timing 有未完成任务，未重启。" }
}
Stop-Process -Id $timingProcess.ProcessId
# A venv launcher keeps its own process while the server runs; clear it too.
$timingParent = Get-CimInstance Win32_Process -Filter "ProcessId=$($timingProcess.ParentProcessId)" -ErrorAction SilentlyContinue
if ($timingParent -and $timingParent.Name -like "python*" -and $timingParent.ExecutablePath -eq $timingPython) {
    Stop-Process -Id $timingParent.ProcessId -ErrorAction SilentlyContinue
}
for ($timingTry = 0; $timingTry -lt 20; $timingTry++) {
    Start-Sleep -Milliseconds 250
    if (-not (Get-NetTCPConnection -LocalPort 7861 -State Listen -ErrorAction SilentlyContinue)) { break }
}
Remove-Item -LiteralPath $timingPidFile -ErrorAction SilentlyContinue
Write-Host "Timing 已关闭。"
