<#
.SYNOPSIS
    Register (or remove) a Windows scheduled task that runs the relay at logon.

.DESCRIPTION
    A process started from an SSH session is killed when that session ends, so
    the relay needs to be launched outside the session - a logon scheduled task
    is the simplest reliable way.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\install_autostart.ps1
    powershell -ExecutionPolicy Bypass -File tools\install_autostart.ps1 -Remove
#>
param(
    [switch]$Remove,
    [string]$TaskName = "XJTLU Relay"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Launcher = Join-Path $Root "start_relay.bat"

if ($Remove) {
    schtasks /Delete /TN $TaskName /F 2>$null
    Write-Host "removed scheduled task: $TaskName"
    exit 0
}

if (-not (Test-Path $Launcher)) {
    throw "launcher not found: $Launcher"
}

schtasks /Create /TN $TaskName /TR $Launcher /SC ONLOGON /RL HIGHEST /F | Out-Null
Write-Host "registered scheduled task '$TaskName' -> $Launcher"
Write-Host ""
Write-Host "It starts automatically at logon. To start it right now:"
Write-Host "  schtasks /Run /TN `"$TaskName`""
