param(
    [string]$IndexUrl = "https://download.pytorch.org/whl/cu126"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Error "Expected venv Python at $Python. Create it first with: python -m venv .venv"
}

Write-Host "Using PyTorch wheel index: $IndexUrl"
Write-Host "Note: the NVIDIA driver may report CUDA 13.2 while PyTorch uses an older bundled CUDA runtime."

& $Python -m pip uninstall -y torch torchvision torchaudio
Remove-Item -LiteralPath (Join-Path $RepoRoot ".venv\Lib\site-packages\torch") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $RepoRoot ".venv\Lib\site-packages\torchvision") -Recurse -Force -ErrorAction SilentlyContinue
$SitePackages = Join-Path $RepoRoot ".venv\Lib\site-packages"
Get-ChildItem -Path $SitePackages -Directory -Filter "torch-*.dist-info" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
Get-ChildItem -Path $SitePackages -Directory -Filter "torchvision-*.dist-info" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
& $Python -m pip install --upgrade pip
& $Python -m pip install --no-cache-dir --force-reinstall torch torchvision --index-url $IndexUrl
& $Python -m pip install -r (Join-Path $RepoRoot "requirements.txt")

& $Python -c "import torch; print('torch', torch.__version__); print('torch CUDA', torch.version.cuda); print('cuda available', torch.cuda.is_available()); print('device count', torch.cuda.device_count())"
