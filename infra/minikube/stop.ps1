$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$minikubePath = Join-Path $projectRoot ".tools\minikube.exe"

& $minikubePath stop --profile stratos
if ($LASTEXITCODE -ne 0) { throw "Minikube failed to stop the stratos profile." }
