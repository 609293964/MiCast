$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

& (Join-Path $PSScriptRoot "build-windows.ps1")

$portable = Join-Path $root "dist\MiCast-Portable"
if (Test-Path $portable) { Remove-Item -LiteralPath $portable -Recurse -Force }
New-Item -ItemType Directory -Path $portable | Out-Null
Copy-Item -LiteralPath (Join-Path $root "dist\MiCast.exe") -Destination $portable
New-Item -ItemType File -Path (Join-Path $portable "portable.flag") | Out-Null
Compress-Archive -Path (Join-Path $portable "*") -DestinationPath (Join-Path $root "dist\MiCast-Portable.zip") -Force

$iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if (-not $iscc) {
  $candidate = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
  if (Test-Path $candidate) { $iscc = Get-Item $candidate }
}
if (-not $iscc) {
  $candidate = Join-Path $env:LOCALAPPDATA "Programs\Inno\ISCC.exe"
  if (Test-Path $candidate) { $iscc = Get-Item $candidate }
}
if (-not $iscc) {
  throw "Inno Setup 6 is not installed. The portable archive was created; install Inno Setup and run this script again."
}
$isccPath = if ($iscc.Source) { $iscc.Source } else { $iscc.FullName }
$version = $env:MICAST_VERSION
if (-not $version) {
  $versionLine = Select-String -LiteralPath (Join-Path $root "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
  if (-not $versionLine) { throw "Could not read the version from pyproject.toml." }
  $version = $versionLine.Matches[0].Groups[1].Value
}
& $isccPath "/DAppVersion=$version" (Join-Path $root "packaging\windows\MiCast.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed: $LASTEXITCODE" }
