[CmdletBinding()]
param(
    [switch]$Lan
)

$ErrorActionPreference = "Stop"
$address = if ($Lan) { "0.0.0.0" } else { "127.0.0.1" }

if ($Lan) {
    Write-Warning "LAN mode exposes the unauthenticated development API to your local network."
}

Write-Host "Forwarding the Stratos API on ${address}:8000. Press Ctrl+C to stop."
kubectl --context stratos -n stratos port-forward --address $address service/controller 8000:8000
