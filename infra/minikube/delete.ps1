$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$minikubePath = Join-Path $projectRoot ".tools\minikube.exe"

Write-Warning "This permanently deletes the local 'stratos' cluster and all data stored in it."
& $minikubePath delete --profile stratos
if ($LASTEXITCODE -ne 0) { throw "Minikube failed to delete the stratos profile." }
