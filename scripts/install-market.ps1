#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Source,
    [string]$ConfigDirectory = (Join-Path $env:USERPROFILE '.workbuddy'),
    [string]$StateDirectory = (Join-Path $env:USERPROFILE '.fiboo\marketplace'),
    [string]$TaskName = 'FIBOO-Marketplace-UpdateCheck',
    [string]$PythonPath
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$distribution = Join-Path $root 'distribution.json'
if (!$Source -and (Test-Path -LiteralPath $distribution)) {
    $Source = (Get-Content -LiteralPath $distribution -Raw -Encoding UTF8 | ConvertFrom-Json).source
}
if (!$Source) { $Source = Read-Host 'Company Git repository URL (ask your FIBOO administrator)' }
if (!$Source) { throw 'A company repository URL is required.' }
$managerArguments = @('--config-dir', $ConfigDirectory, 'register', '--source', $Source)
& (Join-Path $PSScriptRoot 'workbuddy-market.ps1') -PythonPath $PythonPath -Arguments $managerArguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
try {
    & (Join-Path $PSScriptRoot 'install-update-monitor.ps1') -Source $Source -ConfigDirectory $ConfigDirectory `
        -StateDirectory $StateDirectory -TaskName $TaskName -PythonPath $PythonPath -RunNow
} catch {
    Write-Warning 'The marketplace is registered, but the Windows update monitor could not be installed. Ask your administrator to allow your own FIBOO scheduled task and rerun this installer.'
    throw
}
Write-Host 'Marketplace registered. Native auto-update is enabled; a read-only update check runs daily and at sign-in. Open WorkBuddy Skills > Plugins to choose what to install.'
