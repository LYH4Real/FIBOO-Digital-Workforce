[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$PythonPath,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

if (-not $PythonPath) {
    $bundled = Join-Path $env:USERPROFILE '.workbuddy\binaries\python'
    $environmentPython = Join-Path $bundled 'envs\default\Scripts\python.exe'
    if (Test-Path -LiteralPath $environmentPython -PathType Leaf) { $PythonPath = $environmentPython }
}
if (-not $PythonPath) {
    $found = Get-ChildItem -LiteralPath $bundled -Filter python.exe -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\versions\\' } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($found) { $PythonPath = $found.FullName }
}
if (-not $PythonPath) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found) { $PythonPath = $found.Source }
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw '未找到 Python。请先启动 WorkBuddy 完成内置工具安装，或传入 -PythonPath。'
}
& $PythonPath (Join-Path $PSScriptRoot 'workbuddy_market.py') @Arguments
exit $LASTEXITCODE
