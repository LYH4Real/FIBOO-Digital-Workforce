$ErrorActionPreference = 'Stop'
$exe = Join-Path $PSScriptRoot '../runtime/waveeee-image-mcp.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'Missing bundled Wave MCP executable.' }
$previousKey = $env:WAVEEEE_API_KEY
$previousOutputEncoding = $OutputEncoding
try {
    $env:WAVEEEE_API_KEY = ''
    # MCP is UTF-8 JSON lines; PowerShell must not prepend a BOM to stdin.
    $OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $requests = @(
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
        '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
        '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
        '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"server_info","arguments":{}}}'
    )
    $responses = @($requests | & $exe | ForEach-Object { $_ | ConvertFrom-Json })
    if ($LASTEXITCODE -ne 0) { throw 'Wave MCP exited with an error.' }
    $initialized = $responses | Where-Object id -eq 1
    $listed = $responses | Where-Object id -eq 2
    $info = $responses | Where-Object id -eq 3
    $expected = @('edit_batch_images', 'edit_image', 'generate_batch_images', 'generate_image', 'save_image_response', 'server_info')
    $actual = @($listed.result.tools.name | Sort-Object)
    if ($initialized.result.serverInfo.version -ne '0.4.2') { throw 'Unexpected Wave server version.' }
    if (@(Compare-Object $expected $actual).Count -ne 0) { throw 'Unexpected Wave tool list.' }
    if ($info.result.structuredContent.api_key_configured -ne $false) { throw 'Offline check unexpectedly found an API credential.' }
    Write-Output '[PASS] Wave MCP: initialize, 6 tools, server_info, no API credential or network generation.'
} finally {
    $env:WAVEEEE_API_KEY = $previousKey
    $OutputEncoding = $previousOutputEncoding
}
