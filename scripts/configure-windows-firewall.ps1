#Requires -RunAsAdministrator
param(
    [string]$Program = (Join-Path (Split-Path -Parent $PSScriptRoot) ".venv\Scripts\python.exe")
)

$ErrorActionPreference = "Stop"
$Program = (Resolve-Path -LiteralPath $Program).Path

$rules = @("TCP", "UDP")

foreach ($item in $rules) {
    $name = "MiCast Local $item"
    $existing = Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
    if ($existing) {
        $existing | Set-NetFirewallRule -Enabled True -Action Allow -Profile Private
        $existing | Get-NetFirewallApplicationFilter | Set-NetFirewallApplicationFilter -Program $Program
        $existing | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -RemoteAddress LocalSubnet
        $existing | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter `
            -Protocol $item -LocalPort Any
    } else {
        New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow `
            -Profile Private -Protocol $item -Program $Program -RemoteAddress LocalSubnet | Out-Null
    }
}

Write-Host "MiCast private-network firewall rules are ready for $Program."
