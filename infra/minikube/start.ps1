[CmdletBinding()]
param(
    [ValidateRange(3, 5)]
    [int]$WorkerNodes = 3,

    [ValidateRange(2, 8)]
    [int]$CpuPerNode = 2,

    [ValidateRange(2048, 8192)]
    [int]$MemoryMbPerNode = 2048
)

$ErrorActionPreference = "Stop"
$profile = "stratos"
$kubernetesVersion = "v1.37.0"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$minikubePath = Join-Path $projectRoot ".tools\minikube.exe"
$manifestsPath = Join-Path $projectRoot "k8s"
$totalNodes = $WorkerNodes + 1

if (-not (Test-Path -LiteralPath $minikubePath)) {
    throw "Minikube is missing. Run .\infra\minikube\install-minikube.ps1 first."
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is missing. Install and start Docker Desktop first."
}

if (-not (Get-Command kubectl -ErrorAction SilentlyContinue)) {
    throw "kubectl is missing. Install kubectl or enable it in Docker Desktop first."
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is not running or its Linux container engine is unavailable."
}

Write-Host "Starting '$profile' with 1 control plane and $WorkerNodes workers..."
& $minikubePath start `
    --profile $profile `
    --driver docker `
    --container-runtime containerd `
    --kubernetes-version $kubernetesVersion `
    --nodes $totalNodes `
    --cpus $CpuPerNode `
    --memory $MemoryMbPerNode `
    --wait "apiserver,system_pods,node_ready" `
    --wait-timeout 10m
if ($LASTEXITCODE -ne 0) { throw "Minikube failed to start." }

$nodeData = kubectl --context $profile get nodes -o json | ConvertFrom-Json
$workerNames = @(
    $nodeData.items |
        Where-Object {
            $_.metadata.labels.PSObject.Properties.Name -notcontains "node-role.kubernetes.io/control-plane"
        } |
        ForEach-Object { $_.metadata.name }
)

if ($workerNames.Count -ne $WorkerNodes) {
    throw "Expected $WorkerNodes worker nodes but found $($workerNames.Count)."
}

foreach ($nodeName in $workerNames) {
    kubectl --context $profile label node $nodeName `
        "node-role.kubernetes.io/worker=worker" `
        "stratos.dev/worker=true" `
        --overwrite | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Failed to label worker node $nodeName." }
}

Write-Host "Building and loading the Stratos image..."
docker build --tag "stratos:dev" $projectRoot
if ($LASTEXITCODE -ne 0) { throw "The Stratos image build failed." }
& $minikubePath image load --profile $profile "stratos:dev"
if ($LASTEXITCODE -ne 0) { throw "Loading the Stratos image into Minikube failed." }

Write-Host "Applying Kubernetes manifests..."
kubectl --context $profile apply -k $manifestsPath
if ($LASTEXITCODE -ne 0) { throw "The Kubernetes manifests failed to apply." }

# The development image keeps a stable tag, so force application pods to pick up
# the image that was just rebuilt and loaded.
kubectl --context $profile -n stratos rollout restart deployment/controller deployment/worker
if ($LASTEXITCODE -ne 0) { throw "Restarting the application deployments failed." }

kubectl --context $profile -n stratos rollout status deployment/redis --timeout=5m
kubectl --context $profile -n stratos rollout status deployment/controller --timeout=5m
kubectl --context $profile -n stratos rollout status deployment/worker --timeout=5m

Write-Host ""
Write-Host "Cluster is ready."
kubectl --context $profile get nodes -L stratos.dev/worker
kubectl --context $profile -n stratos get pods -o wide
Write-Host ""
Write-Host "Run .\infra\minikube\access-api.ps1 and open http://localhost:8000/docs"
