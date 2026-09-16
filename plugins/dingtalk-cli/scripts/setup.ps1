#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$InstallDirectory = (Join-Path $env:USERPROFILE '.fiboo\bin'),
    [switch]$NoPath,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$source = Join-Path (Split-Path $PSScriptRoot -Parent) 'bin\dws.exe'
$target = Join-Path $InstallDirectory 'dws.exe'
if (!(Test-Path -LiteralPath $source -PathType Leaf)) { throw 'Bundled DWS executable is missing.' }
$expected = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
$current = if (Test-Path -LiteralPath $target -PathType Leaf) { (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash } else { '' }
if ($CheckOnly) {
    [pscustomobject]@{ ready = ($expected -eq $current); binary = $target; bundledSha256 = $expected } | ConvertTo-Json
    if ($expected -ne $current) { exit 2 }
    exit 0
}
if ($expected -ne $current) {
    New-Item -ItemType Directory -Force -Path $InstallDirectory | Out-Null
    $staging = Join-Path $InstallDirectory ('dws-' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        Copy-Item -LiteralPath $source -Destination $staging
        if ((Get-FileHash -LiteralPath $staging -Algorithm SHA256).Hash -ne $expected) { throw 'DWS checksum mismatch.' }
        Move-Item -LiteralPath $staging -Destination $target -Force
    } finally {
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Force }
    }
}
if (!$NoPath) {
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $parts = @($userPath -split ';' | Where-Object { $_ -and $_.TrimEnd('\') -ine $InstallDirectory.TrimEnd('\') })
    [Environment]::SetEnvironmentVariable('Path', (($InstallDirectory) + ';' + ($parts -join ';')), 'User')
    $env:Path = $InstallDirectory + ';' + $env:Path
}
& $target version
if ($LASTEXITCODE -ne 0) { throw 'DWS version check failed.' }
Write-Output 'DWS is ready. Existing credentials were not changed. Restart WorkBuddy to refresh PATH, or use the absolute binary path.'
