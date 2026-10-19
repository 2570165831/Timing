param([switch]$NoBrowser, [switch]$ForceBaseInterpreter, [switch]$ForceCopyInterpreter)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$timingUrl = "http://127.0.0.1:7861"
$runtime = Join-Path $PSScriptRoot ".runtime"
$venv = Join-Path $PSScriptRoot ".venv"
$timingPython = Join-Path $venv "Scripts\python.exe"
$localPython = Join-Path $runtime "python312\python.exe"
$sitePackages = Join-Path $venv "Lib\site-packages"
$timingLog = Join-Path $runtime "launch.log"

function Write-TimingLog([string]$message) {
    Add-Content -LiteralPath $timingLog -Value ("[{0}] {1}" -f (Get-Date -Format "s"), $message)
}

function Test-TimingRunning {
    try {
        $response = Invoke-RestMethod "$timingUrl/api/status" -TimeoutSec 2
        return $response.app -eq "Timing"
    } catch { return $false }
}

function Test-TimingPython([string]$exe) {
    $probe = & $exe -X utf8 -c "import sys; print(sys.version.split()[0])" 2>&1
    return @{ Ok = ($LASTEXITCODE -eq 0); Detail = (($probe | Out-String).Trim()) }
}

function Get-BasePython {
    $cfg = Join-Path $venv "pyvenv.cfg"
    if (-not (Test-Path -LiteralPath $cfg)) { return $null }
    $line = Select-String -LiteralPath $cfg -Pattern "^\s*home\s*=\s*(.+?)\s*$" | Select-Object -First 1
    if (-not $line) { return $null }
    return Join-Path $line.Matches[0].Groups[1].Value "python.exe"
}

# The uv-managed interpreter in %APPDATA% has been unreadable for several seconds at a time,
# which kills both the venv launcher and any child started from that path. Keep a private
# copy inside .runtime so a launch never depends on it.
function Get-LocalInterpreter {
    if (Test-Path -LiteralPath $localPython) { return $localPython }
    $basePython = Get-BasePython
    if (-not $basePython) {
        Write-TimingLog "本地解释器缺失，且 pyvenv.cfg 里没有 home。"
        return $null
    }
    try {
        Copy-Item -LiteralPath (Split-Path -Parent $basePython) -Destination (Join-Path $runtime "python312") -Recurse -Force
        Write-TimingLog "已从 $basePython 建立本地解释器副本。"
    } catch {
        Write-TimingLog ("建立本地解释器副本失败：" + $_.Exception.Message)
        return $null
    }
    if (Test-Path -LiteralPath $localPython) { return $localPython }
    return $null
}

$timingStartupLock = $null
$timingInstallFolder = Join-Path $runtime 'install'
New-Item -ItemType Directory -Path $timingInstallFolder -Force | Out-Null
try {
    try { $timingStartupLock = [IO.File]::Open((Join-Path $timingInstallFolder 'setup.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None) }
    catch { throw '安装或启动正在进行中，请等当前窗口完成后再启动 Timing。' }

    if (Test-TimingRunning) {
        if (-not $NoBrowser) { Start-Process $timingUrl }
        Write-Host "Timing: $timingUrl"
        return
    }

    if (-not (Test-Path -LiteralPath $timingPython)) { throw "请先运行 setup.ps1。" }
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null

    $venvEnvironment = @{ PYTHONPATH = $sitePackages; VIRTUAL_ENV = $venv }
    $candidates = @()
    if ($ForceBaseInterpreter) {
        $candidates += @{ Path = (Get-BasePython); Environment = $venvEnvironment; Name = "基础解释器" }
    } elseif ($ForceCopyInterpreter) {
        $candidates += @{ Path = (Get-LocalInterpreter); Environment = $venvEnvironment; Name = "本地副本" }
    } else {
        $candidates += @{ Path = $timingPython; Environment = @{}; Name = "虚拟环境启动器" }
        $candidates += @{ Path = (Get-LocalInterpreter); Environment = $venvEnvironment; Name = "本地副本" }
        $candidates += @{ Path = (Get-BasePython); Environment = $venvEnvironment; Name = "基础解释器" }
    }

    $runner = $null
    $environment = @{}
    foreach ($candidate in $candidates) {
        if (-not $candidate.Path) { continue }
        for ($probeTry = 1; $probeTry -le 3; $probeTry++) {
            $check = Test-TimingPython $candidate.Path
            if ($check.Ok) {
                Write-TimingLog ("$($candidate.Name)自检通过：$($candidate.Path)（$($check.Detail)）")
                $runner = $candidate.Path
                $environment = $candidate.Environment
                break
            }
            Write-TimingLog ("$($candidate.Name)第 $probeTry 次自检失败（file-exists=$([System.IO.File]::Exists($candidate.Path))）：$($check.Detail)")
            Start-Sleep -Milliseconds 800
        }
        if ($runner) { break }
    }
    if (-not $runner) {
        throw "没有可用的 Python 解释器。请重新运行 setup.ps1；记录见 .runtime/launch.log。"
    }

    $timingRunning = $false
    foreach ($name in $environment.Keys) { Set-Item -Path ("Env:" + $name) -Value $environment[$name] }
    for ($timingAttempt = 1; $timingAttempt -le 2; $timingAttempt++) {
        $timingProcess = Start-Process -FilePath $runner -ArgumentList @("-X", "utf8", "-u", "-m", "timing", "--serve") -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtime "server.log") -RedirectStandardError (Join-Path $runtime "server-error.log")
        $timingProcess.Id | Set-Content -LiteralPath (Join-Path $runtime "server.pid")
        for ($timingTry = 0; $timingTry -lt 30; $timingTry++) {
            Start-Sleep -Milliseconds 500
            if (Test-TimingRunning) { $timingRunning = $true; break }
            if ($timingProcess.HasExited) { break }
        }
        if ($timingRunning) {
            Write-TimingLog ("服务已启动：进程 {0}，解释器 {1}，第 {2} 次尝试" -f $timingProcess.Id, $runner, $timingAttempt)
            break
        }
        if (-not $timingProcess.HasExited) { break }
        $timingError = ((Get-Content (Join-Path $runtime "server-error.log") -Raw -ErrorAction SilentlyContinue) | Out-String).Trim()
        Write-TimingLog ("第 $timingAttempt 次启动失败：$timingError")
    }
    foreach ($name in $environment.Keys) { Remove-Item -Path ("Env:" + $name) -ErrorAction SilentlyContinue }
    if (-not $timingRunning) { throw "启动失败，请查看 .runtime/server-error.log 与 .runtime/launch.log。" }
    if (-not $NoBrowser) { Start-Process $timingUrl }
    Write-Host "Timing: $timingUrl"
} finally {
    if ($timingStartupLock) { $timingStartupLock.Dispose() }
}
