#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$InstallDirectory = (Join-Path $env:USERPROFILE '.kimi-webbridge\bin'),
    [switch]$NoStart,
    [switch]$CheckOnly,
    [switch]$OpenExtensionPage
)
$ErrorActionPreference = 'Stop'
$runtime = Get-Content -LiteralPath (Join-Path (Split-Path $PSScriptRoot -Parent) 'runtime.json') -Raw | ConvertFrom-Json
$binary = Join-Path $InstallDirectory 'kimi-webbridge.exe'
$status = $null
try { $status = Invoke-RestMethod -Uri 'http://127.0.0.1:10086/status' -TimeoutSec 3 } catch { }
if ($CheckOnly) {
    [pscustomobject]@{ installed = (Test-Path -LiteralPath $binary); version = $status.version; connected = [bool]$status.extension_connected; updateAvailable = $status.update_available; versionMismatch = $status.version_mismatch } | ConvertTo-Json -Depth 5
    if (!$status.extension_connected) { exit 2 }
    exit 0
}
if (!(Test-Path -LiteralPath $binary)) {
    New-Item -ItemType Directory -Force -Path $InstallDirectory | Out-Null
    $temporary = Join-Path $InstallDirectory ('kimi-' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $runtime.url -OutFile $temporary -UseBasicParsing -TimeoutSec 90
        if ((Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash -ine $runtime.sha256) { throw 'Kimi download checksum mismatch.' }
        Move-Item -LiteralPath $temporary -Destination $binary
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
} else {
    Write-Output 'Existing Kimi runtime retained. Use its status/upgrade command to manage version alignment with the browser extension.'
}
if (!$NoStart) {
    & $binary start
    if ($LASTEXITCODE -ne 0) { throw 'Kimi daemon did not start.' }
}
if ($OpenExtensionPage) { Start-Process $runtime.extensionPage }
Write-Output ('Install the browser extension from: ' + $runtime.extensionPage)
Write-Output 'Then run setup.ps1 -CheckOnly. Browser-store extension installation requires the employee to click Add.'
