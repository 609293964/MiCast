$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

Set-Location -LiteralPath $ProjectRoot
if (-not (Test-Path -LiteralPath $Python)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Python environment creation failed" }
}

& $Python -c "import micast, av, Crypto, zeroconf" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[MiCast] Installing Python dependencies..."
    & $Python -m pip install -e .
    if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed" }
}

if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "web\node_modules"))) {
    npm --prefix web ci
    if ($LASTEXITCODE -ne 0) { throw "Web dependency installation failed" }
}
npm --prefix web run build
if ($LASTEXITCODE -ne 0) { throw "Web build failed" }

$env:MICAST_AIRPLAY_ENGINE = "local"
Write-Host "[MiCast] 正在启动：http://127.0.0.1:3000/app/micast/"
& $Python -m uvicorn micast.main:app --host 0.0.0.0 --port 3000
exit $LASTEXITCODE
