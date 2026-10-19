@echo off
chcp 65001 >nul
setlocal DisableDelayedExpansion
cd /d "%~dp0"
where pwsh.exe >nul 2>nul
if errorlevel 1 goto probe_known
pwsh.exe -NoProfile -Command "if ($PSVersionTable.PSVersion.Major -ge 7) { exit 0 }; exit 1" >nul 2>nul
if not errorlevel 1 goto run
:probe_known
if not exist "%ProgramFiles%\PowerShell\7\pwsh.exe" goto check_args
"%ProgramFiles%\PowerShell\7\pwsh.exe" -NoProfile -Command "if ($PSVersionTable.PSVersion.Major -ge 7) { exit 0 }; exit 1" >nul 2>nul
if not errorlevel 1 goto known

:check_args
if "%~1"=="" goto need_pwsh
if /I "%~1"=="-CheckOnly" goto check_missing
shift
goto check_args

:need_pwsh
echo 首次安装需要 PowerShell 7，请先安装。此入口不会使用 Windows PowerShell 5.1。
echo 下载地址：https://learn.microsoft.com/powershell/scripting/install/install-powershell-on-windows
where winget.exe >nul 2>nul
if errorlevel 1 goto no_winget
choice /C YN /N /M "通过 WinGet 安装 PowerShell 7？[Y/N] "
if errorlevel 2 exit /b 1
winget install --id Microsoft.PowerShell --exact --source winget
if errorlevel 1 goto failed
if exist "%ProgramFiles%\PowerShell\7\pwsh.exe" goto known
where pwsh.exe >nul 2>nul
if not errorlevel 1 goto run
echo PowerShell 已安装，请关闭当前窗口，重新双击 安装.cmd。
pause
exit /b 1
:known
"%ProgramFiles%\PowerShell\7\pwsh.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -InstallPrerequisites %*
goto result
:run
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -InstallPrerequisites %*
:result
set "timingExit=%errorlevel%"
pause
exit /b %timingExit%
:no_winget
echo 没有找到 WinGet，请从上面的地址安装 PowerShell 7，再运行此文件。
pause
exit /b 1
:failed
echo 安装未完成，请根据上面的提示处理后重试。
pause
exit /b 1
:check_missing
echo 仅检查：未找到 PowerShell 7，检查未完成；没有安装或下载任何工具。
echo 请先安装 PowerShell 7：https://learn.microsoft.com/powershell/scripting/install/install-powershell-on-windows
pause
exit /b 1
