$ErrorActionPreference = 'Stop'
$exe = Join-Path $PSScriptRoot '../runtime/xhs-note-mcp.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'Missing bundled XHS MCP executable.' }
& $exe --self-test
if ($LASTEXITCODE -ne 0) { throw 'XHS MCP offline protocol check failed.' }
