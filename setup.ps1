#requires -Version 7.0
param(
    [switch]$SkipModels,
    [switch]$CheckOnly,
    [switch]$InstallPrerequisites
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts/setup_helpers.ps1')
try {
    exit (Invoke-TimingSetup -Root $PSScriptRoot -SkipModels:$SkipModels -CheckOnly:$CheckOnly -InstallPrerequisites:$InstallPrerequisites)
} catch {
    Write-Host ('安装未完成：' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
