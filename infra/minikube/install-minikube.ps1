[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$version = "v1.39.0"
$expectedSha256 = "776386465ded2cf610ae397fe302de32c44d12134b5ce7ce98ab05bd7713b360"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$toolsDirectory = Join-Path $projectRoot ".tools"
$minikubePath = Join-Path $toolsDirectory "minikube.exe"
$downloadUrl = "https://github.com/kubernetes/minikube/releases/download/$version/minikube-windows-amd64.exe"

New-Item -ItemType Directory -Force -Path $toolsDirectory | Out-Null

if (-not (Test-Path -LiteralPath $minikubePath)) {
    Write-Host "Downloading Minikube $version..."
    Invoke-WebRequest -Uri $downloadUrl -OutFile $minikubePath -UseBasicParsing
}

$actualSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $minikubePath).Hash.ToLowerInvariant()
if ($actualSha256 -ne $expectedSha256) {
    throw "Minikube checksum mismatch. Expected $expectedSha256 but received $actualSha256."
}

Write-Host "Minikube is installed and verified at $minikubePath"
& $minikubePath version --short
