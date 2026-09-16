#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Source,
    [string]$ConfigDirectory = (Join-Path $env:USERPROFILE '.workbuddy'),
    [string]$StateDirectory = (Join-Path $env:USERPROFILE '.fiboo\marketplace'),
    [string]$TaskName = 'FIBOO-Marketplace-UpdateCheck',
    [string]$PythonPath,
    [switch]$RunNow,
    [switch]$NoNotify,
    [switch]$CheckOnly,
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
if ($TaskName -notmatch '^FIBOO-[A-Za-z0-9._-]+$') { throw 'TaskName must begin with FIBOO- and contain only letters, digits, dots, underscores or hyphens.' }
$StateDirectory = [System.IO.Path]::GetFullPath($StateDirectory)
$ConfigDirectory = [System.IO.Path]::GetFullPath($ConfigDirectory)
if ($StateDirectory -match '["\r\n]') { throw 'StateDirectory cannot contain quotation marks or line breaks.' }
$taskPath = '\FIBOO\'
$description = 'FIBOO read-only marketplace update monitor; StateDirectory=' + $StateDirectory
$existing = Get-ScheduledTask -TaskName $TaskName -TaskPath $taskPath -ErrorAction SilentlyContinue
if ($existing -and $existing.Description -ne $description) { throw 'An unrelated task already uses this name. Choose another FIBOO- TaskName; it will not be overwritten.' }
if ($CheckOnly) {
    [pscustomobject]@{ taskName = $TaskName; taskPath = $taskPath; installed = [bool]$existing;
        state = $(if ($existing) { [string]$existing.State } else { 'NotInstalled' });
        statusFile = (Join-Path $StateDirectory 'update-status.json'); report = (Join-Path $StateDirectory 'update-report.html') } | ConvertTo-Json
    exit 0
}
if ($Uninstall) {
    if ($existing) {
        Stop-ScheduledTask -TaskName $TaskName -TaskPath $taskPath -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -TaskPath $taskPath -Confirm:$false
    }
    Write-Output 'FIBOO update-check task removed. Reports and scripts are retained for inspection; installed WorkBuddy plugins were not changed.'
    exit 0
}
if ($Source) {
    if ($Source -match '[\r\n\x00]' -or $Source.StartsWith('-')) { throw 'Invalid market source.' }
    if (!(Test-Path -LiteralPath $Source -PathType Container)) {
        if ($Source -match '^https://') {
            $uri = [Uri]$Source
            if (!$uri.Host -or $uri.UserInfo -or $uri.Query -or $uri.Fragment) { throw 'Repository URLs must not contain credentials, queries or fragments.' }
        } elseif ($Source -match '^ssh://') {
            $uri = [Uri]$Source
            if (!$uri.Host -or $uri.UserInfo.Contains(':') -or $uri.Query -or $uri.Fragment) { throw 'SSH URLs must not contain passwords, queries or fragments.' }
        } elseif ($Source -notmatch '^[\w.-]+@[\w.-]+:[\w./-]+$') { throw 'Use a local market directory or credential-free HTTPS/SSH Git URL.' }
    } else { $Source = (Resolve-Path -LiteralPath $Source).Path }
}
$scriptDirectory = Join-Path $StateDirectory 'scripts'
New-Item -ItemType Directory -Path $scriptDirectory -Force | Out-Null
foreach ($filename in @('update-monitor.ps1', 'workbuddy_market.py', 'install-update-monitor.ps1')) {
    $origin = Join-Path $PSScriptRoot $filename
    $destination = Join-Path $scriptDirectory $filename
    if (!(Test-Path -LiteralPath $origin -PathType Leaf)) { throw ('Required monitor file is missing: ' + $filename) }
    if ([System.IO.Path]::GetFullPath($origin) -ne [System.IO.Path]::GetFullPath($destination)) {
        Copy-Item -LiteralPath $origin -Destination $destination -Force
    }
}
$configuration = [ordered]@{ schemaVersion = 1; managedBy = 'FIBOO-Update-Monitor'; source = $Source;
    configDirectory = $ConfigDirectory; pythonPath = $PythonPath; taskName = $TaskName; taskPath = $taskPath;
    notificationsEnabled = !$NoNotify }
$json = $configuration | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText((Join-Path $StateDirectory 'monitor-config.json'), $json, [System.Text.UTF8Encoding]::new($false))
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$monitor = Join-Path $scriptDirectory 'update-monitor.ps1'
$arguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $monitor + '" -StateDirectory "' + $StateDirectory + '"'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $scriptDirectory
$triggers = @((New-ScheduledTaskTrigger -Daily -At '10:00'), (New-ScheduledTaskTrigger -AtLogOn -User $identity))
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -TaskPath $taskPath -Description $description -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Force | Out-Null
if ($RunNow) { Start-ScheduledTask -TaskName $TaskName -TaskPath $taskPath }
[pscustomobject]@{ installed = $true; taskName = $TaskName; taskPath = $taskPath; readOnly = $true;
    schedule = 'Daily at 10:00 local time and at current-user logon';
    statusFile = (Join-Path $StateDirectory 'update-status.json'); report = (Join-Path $StateDirectory 'update-report.html') } | ConvertTo-Json
