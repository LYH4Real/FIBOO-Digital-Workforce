param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$pluginRoot = Split-Path $PSScriptRoot -Parent
$sourceRoot = Join-Path $pluginRoot 'source'
$buildRoot = Join-Path $pluginRoot '.build'
$runtimeRoot = Join-Path $pluginRoot 'runtime'
& $Python -c "import sys,struct,PyInstaller; assert sys.platform == 'win32' and struct.calcsize('P') == 8, 'Windows x64 required'"
if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11+ x64 and PyInstaller in a build environment first.' }
New-Item -ItemType Directory -Force -Path $buildRoot, $runtimeRoot | Out-Null
Push-Location $sourceRoot
try {
    & $Python -m PyInstaller --noconfirm --clean --onefile --noupx --name waveeee-image-mcp --distpath $runtimeRoot --workpath $buildRoot --specpath $buildRoot --paths $sourceRoot (Join-Path $sourceRoot 'run_server.py')
    if ($LASTEXITCODE -ne 0) { throw 'Wave MCP build failed.' }
} finally { Pop-Location }
& (Join-Path $PSScriptRoot 'check.ps1')
