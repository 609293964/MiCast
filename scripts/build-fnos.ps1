param(
    [ValidateSet("x86", "arm")]
    [string]$Platform = "x86",
    [string]$Fnpack = "",
    [string]$StageDir = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $StageDir) {
    $StageDir = Join-Path $ProjectRoot "build\fnos\micast"
} elseif (-not [IO.Path]::IsPathRooted($StageDir)) {
    $StageDir = Join-Path $ProjectRoot $StageDir
}
$OutputDir = Join-Path $ProjectRoot "dist\fnos"
$TemplateDir = Join-Path $ProjectRoot "packaging\fnos"

if (-not $Fnpack) {
    $Fnpack = Join-Path $ProjectRoot "build-tools\fnpack.exe"
}
if (-not (Test-Path -LiteralPath $Fnpack)) {
    $FnpackDir = Split-Path -Parent $Fnpack
    New-Item -ItemType Directory -Force -Path $FnpackDir | Out-Null
    $FnpackUrl = "https://static2.fnnas.com/fnpack/fnpack-1.2.3-windows-amd64"
    Write-Host "Downloading fnpack 1.2.3 from the fnOS developer site..."
    Invoke-WebRequest -UseBasicParsing -Uri $FnpackUrl -OutFile $Fnpack
}
if ($Platform -ne "x86") {
    throw "The Windows cross-build currently supports x86 only. Use build-fnos.sh on ARM64 Linux for ARM."
}

if (Test-Path -LiteralPath $StageDir) {
    Remove-Item -LiteralPath $StageDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $StageDir, $OutputDir | Out-Null
Copy-Item -Path (Join-Path $TemplateDir "*") -Destination $StageDir -Recurse -Force
New-Item -ItemType Directory -Force -Path `
    (Join-Path $StageDir "app\vendor"), `
    (Join-Path $StageDir "app\web"), `
    (Join-Path $StageDir "app\ui\images"), `
    (Join-Path $StageDir "wizard") | Out-Null

& npm --prefix (Join-Path $ProjectRoot "web") run build
if ($LASTEXITCODE -ne 0) { throw "Web build failed" }

Copy-Item -LiteralPath (Join-Path $ProjectRoot "micast") -Destination (Join-Path $StageDir "app") -Recurse
Copy-Item -LiteralPath (Join-Path $ProjectRoot "web\dist") -Destination (Join-Path $StageDir "app\web") -Recurse

& python -m pip install `
    --disable-pip-version-check `
    --no-compile `
    --only-binary=:all: `
    --platform manylinux_2_28_x86_64 `
    --platform manylinux_2_17_x86_64 `
    --platform manylinux2014_x86_64 `
    --implementation cp `
    --python-version 3.12 `
    --abi cp312 `
    --target (Join-Path $StageDir "app\vendor") `
    --requirement (Join-Path $ProjectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Downloading Linux x86 Python dependencies failed" }

$ManifestPath = Join-Path $StageDir "manifest"
$ManifestContent = ([System.IO.File]::ReadAllText(
    $ManifestPath,
    [System.Text.Encoding]::UTF8
)) `
    -replace '(?m)^platform=.*$', "platform=$Platform"
[System.IO.File]::WriteAllText(
    $ManifestPath,
    $ManifestContent,
    [System.Text.UTF8Encoding]::new($false)
)

Copy-Item -LiteralPath (Join-Path $ProjectRoot "web\public\icons\fnos-64.png") -Destination (Join-Path $StageDir "ICON.PNG")
Copy-Item -LiteralPath (Join-Path $ProjectRoot "web\public\icons\fnos-256.png") -Destination (Join-Path $StageDir "ICON_256.PNG")
Copy-Item -LiteralPath (Join-Path $ProjectRoot "web\public\icons\fnos-64.png") -Destination (Join-Path $StageDir "app\ui\images\icon_64.png")
Copy-Item -LiteralPath (Join-Path $ProjectRoot "web\public\icons\fnos-256.png") -Destination (Join-Path $StageDir "app\ui\images\icon_256.png")

Get-ChildItem -Path (Join-Path $StageDir "app") -Directory -Recurse -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem -Path (Join-Path $StageDir "app") -File -Recurse -Include "*.pyc", "*.pyo" | Remove-Item -Force
Get-ChildItem -Path (Join-Path $StageDir "app\vendor") -Directory -Recurse | Where-Object {
    $_.Name -in @("tests", "test", "__pycache__")
} | Sort-Object FullName -Descending | Remove-Item -Recurse -Force
Get-ChildItem -Path (Join-Path $StageDir "app\vendor") -File -Recurse -Include "*.pyi" | Remove-Item -Force

Push-Location $StageDir
try {
    & $Fnpack build
    if ($LASTEXITCODE -ne 0) { throw "fnpack build failed" }
}
finally {
    Pop-Location
}

$Packages = Get-ChildItem -Path $StageDir -Filter "*.fpk" -File
if (-not $Packages) {
    $Packages = Get-ChildItem -Path $ProjectRoot -Filter "*.fpk" -File
}
if (-not $Packages) { throw "fnpack did not produce an .fpk file" }

foreach ($Package in $Packages) {
    $VersionLine = Select-String -LiteralPath $ManifestPath -Pattern '^version=(.+)$'
    $Version = $VersionLine.Matches[0].Groups[1].Value.Trim()
    $OutputName = "micast-$Platform-$Version.fpk"
    Copy-Item -LiteralPath $Package.FullName -Destination (Join-Path $OutputDir $OutputName) -Force
}
Write-Host "fnOS package: $OutputDir"
