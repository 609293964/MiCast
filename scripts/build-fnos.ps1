param(
    [ValidateSet("x86", "arm")]
    [string]$Platform = "x86"
)

# 薄封装：真正的实现在 scripts/build-fnos.sh（需要 bash + fnpack + npm）。
# 本脚本只负责在 Windows 上找到 bash.exe 并透传参数。

$ErrorActionPreference = "Stop"

$Bash = Get-Command bash.exe -ErrorAction SilentlyContinue
if (-not $Bash) {
    $GitBash = "$env:ProgramFiles\Git\bin\bash.exe"
    if (Test-Path -LiteralPath $GitBash) {
        $Bash = Get-Command $GitBash
    }
}
if (-not $Bash) {
    throw "未找到 bash.exe。请安装 Git for Windows（https://git-scm.com/download/win）或启用 WSL 后重试。"
}

$Script = Join-Path $PSScriptRoot "build-fnos.sh"
& $Bash.Source $Script $Platform
if ($LASTEXITCODE -ne 0) { throw "build-fnos.sh failed (exit $LASTEXITCODE)" }
