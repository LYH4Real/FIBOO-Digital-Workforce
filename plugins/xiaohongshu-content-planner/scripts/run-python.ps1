#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Script,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$ScriptArguments
)
$ErrorActionPreference = 'Stop'
$candidates = @()
if ($env:FIBOO_PYTHON) { $candidates += $env:FIBOO_PYTHON }
$buddyHome = if ($env:WORKBUDDY_CONFIG_DIR) { $env:WORKBUDDY_CONFIG_DIR } else { Join-Path $env:USERPROFILE '.workbuddy' }
$candidates += (Join-Path $buddyHome 'binaries\python\envs\default\Scripts\python.exe')
$versions = Join-Path $buddyHome 'binaries\python\versions'
if (Test-Path -LiteralPath $versions) {
    $candidates += @(Get-ChildItem -LiteralPath $versions -Directory | Sort-Object LastWriteTime -Descending | ForEach-Object { Join-Path $_.FullName 'python.exe' })
}
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if ($pythonCommand) { $candidates += $pythonCommand.Source }
$selected = $null
foreach ($candidate in $candidates) {
    if (!(Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
    & $candidate -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
    if ($LASTEXITCODE -eq 0) { $selected = $candidate; break }
}
if (!$selected) { throw 'Python 3.10+ not found. Let WorkBuddy prepare its Python runtime, or set FIBOO_PYTHON to a Python executable.' }
if (!(Test-Path -LiteralPath $Script -PathType Leaf)) { throw 'The requested Python script does not exist.' }
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
& $selected -B $Script @ScriptArguments
exit $LASTEXITCODE
