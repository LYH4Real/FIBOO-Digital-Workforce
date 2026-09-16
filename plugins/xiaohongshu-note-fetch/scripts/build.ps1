param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$pluginRoot = Split-Path $PSScriptRoot -Parent
$sourceRoot = Join-Path $pluginRoot 'source'
$buildRoot = Join-Path $pluginRoot '.build'
& $Python -c "import sys,struct,PyInstaller,mcp; assert sys.platform == 'win32' and struct.calcsize('P') == 8, 'Windows x64 required'"
if ($LASTEXITCODE -ne 0) { throw 'Install Python x64, PyInstaller and mcp>=1.20,<2 in a build environment first.' }
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
Push-Location $sourceRoot
try {
    & $Python -m PyInstaller --noconfirm --clean --onedir --console --noupx --name xhs-note-mcp --distpath (Join-Path $buildRoot 'dist') --workpath (Join-Path $buildRoot 'work') --specpath $buildRoot --paths $sourceRoot --hidden-import anyio._backends._asyncio --collect-data mcp --collect-data jsonschema_specifications --copy-metadata mcp --exclude-module playwright --exclude-module tkinter (Join-Path $sourceRoot 'launcher.py')
    if ($LASTEXITCODE -ne 0) { throw 'XHS MCP build failed.' }
    $builtRuntime = Join-Path $buildRoot 'dist/xhs-note-mcp'
    $runtime = Join-Path $pluginRoot 'runtime'
    & (Join-Path $builtRuntime 'xhs-note-mcp.exe') --self-test
    if ($LASTEXITCODE -ne 0) { throw 'Freshly built XHS runtime failed its offline test; current runtime was preserved.' }
    # Verify all move targets remain inside this plugin before replacing runtime.
    $allowedRoot = [IO.Path]::GetFullPath($pluginRoot).TrimEnd('\') + '\'
    $resolvedRuntime = [IO.Path]::GetFullPath($runtime)
    $backup = [IO.Path]::GetFullPath((Join-Path $buildRoot ('previous-runtime-' + [Guid]::NewGuid().ToString('N'))))
    foreach ($target in @($resolvedRuntime, $backup)) {
        if (-not $target.StartsWith($allowedRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Runtime replacement escaped plugin directory.' }
    }
    if (Test-Path -LiteralPath $runtime) { Move-Item -LiteralPath $runtime -Destination $backup }
    Copy-Item -LiteralPath $builtRuntime -Destination $runtime -Recurse
} finally { Pop-Location }
& (Join-Path $PSScriptRoot 'check.ps1')
Write-Output 'Refresh BUILD_INFO.json, DEPENDENCIES.json, licenses, version and verification hashes before publishing.'
