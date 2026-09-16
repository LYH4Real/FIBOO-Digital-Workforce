#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Source,
    [string]$ConfigDirectory,
    [string]$StateDirectory = (Join-Path $env:USERPROFILE '.fiboo\marketplace'),
    [string]$PythonPath,
    [switch]$NoHeadCache,
    [switch]$NoNotify
)
$ErrorActionPreference = 'Stop'
$settingsPath = Join-Path $StateDirectory 'monitor-config.json'
$settings = if (Test-Path -LiteralPath $settingsPath -PathType Leaf) {
    Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
} else { $null }
if (!$Source -and $settings) { $Source = $settings.source }
if (!$ConfigDirectory -and $settings) { $ConfigDirectory = $settings.configDirectory }
if (!$PythonPath -and $settings) { $PythonPath = $settings.pythonPath }
if ($settings -and $settings.notificationsEnabled -eq $false) { $NoNotify = $true }
if (!$ConfigDirectory) { $ConfigDirectory = Join-Path $env:USERPROFILE '.workbuddy' }
$manager = Join-Path $PSScriptRoot 'workbuddy_market.py'
if (!(Test-Path -LiteralPath $manager -PathType Leaf)) { throw 'The read-only marketplace manager is missing.' }
$candidates = @($PythonPath, $env:FIBOO_PYTHON)
foreach ($buddyRoot in @($ConfigDirectory, (Join-Path $env:USERPROFILE '.workbuddy')) | Select-Object -Unique) {
    $candidates += Join-Path $buddyRoot 'binaries\python\envs\default\Scripts\python.exe'
    $versions = Join-Path $buddyRoot 'binaries\python\versions'
    if (Test-Path -LiteralPath $versions) {
        $candidates += @(Get-ChildItem -LiteralPath $versions -Directory | Sort-Object LastWriteTime -Descending |
            ForEach-Object { Join-Path $_.FullName 'python.exe' })
    }
}
$systemPython = Get-Command python.exe -ErrorAction SilentlyContinue
if ($systemPython) { $candidates += $systemPython.Source }
$selected = $null
foreach ($candidate in $candidates | Where-Object { $_ } | Select-Object -Unique) {
    if (!(Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
    & $candidate -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
    if ($LASTEXITCODE -eq 0) { $selected = $candidate; break }
}
if (!$selected) { throw 'Python 3.10+ is unavailable. Initialize the WorkBuddy runtime or supply -PythonPath.' }
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:GIT_TERMINAL_PROMPT = '0'
$env:GCM_INTERACTIVE = 'Never'
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$program = @'
# BEGIN_PYTHON
import contextlib
from datetime import datetime, timezone
import hashlib
import html
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def load_manager(path):
    spec = importlib.util.spec_from_file_location('fiboo_readonly_manager', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_state(path):
    if not path.is_file():
        return {}
    try:
        if path.stat().st_size > 4_000_000:
            return {}
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def atomic_write(path, text):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         dir=path.parent, prefix='.fiboo-monitor-', suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            output.write(text)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def exclusive_state(state):
    with (state / '.update-monitor.lock').open('a+b') as lock:
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                yield False
                return
            try:
                yield True
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def remote_head(manager, config, source):
    environment = manager.git_environment(config)
    environment.update({'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'Never'})
    # BatchMode avoids a hidden SSH passphrase/host-key prompt. No credentials
    # are accepted by this monitor and nothing is written to credential stores.
    environment.setdefault('GIT_SSH_COMMAND', 'ssh -oBatchMode=yes -oConnectTimeout=15')
    result = subprocess.run([manager.discover_git(config), 'ls-remote', '--exit-code', '--', source, 'HEAD'],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=30, env=environment, creationflags=manager.CREATE_NO_WINDOW)
    if result.returncode:
        raise manager.MarketError('Remote update check failed; retry when network access is available.')
    text = result.stdout.decode('ascii', errors='replace').strip()
    match = re.fullmatch(r'([0-9a-fA-F]{40,64})\s+HEAD', text)
    if not match:
        raise manager.MarketError('Remote HEAD could not be verified.')
    return match.group(1).lower()


def cached_comparisons(manager, config, previous):
    available = previous.get('availableVersions', {})
    installed = manager.installed_status(config)
    entries = installed.pop('installed')
    if any(entry['plugin'] not in available for entry in entries):
        return None
    comparisons = []
    for entry in entries:
        source = available[entry['plugin']]
        comparisons.append({'plugin': entry['plugin'], 'installed': entry['version'],
                            'available': source['version'], 'versionDiffers': not source['missing'] and source['version'] != entry['version'],
                            'missingFromSource': source['missing']})
    return installed['marketplace'], comparisons


def render_report(status):
    title = 'FIBOO-\u6570\u5b57\u5458\u5de5\u5e02\u573a | \u81ea\u52a8\u68c0\u67e5\u7ed3\u679c'
    rows = ''.join('<tr>' + ''.join('<td>' + html.escape(str(row.get(key, ''))) + '</td>'
                                  for key in ('plugin', 'installed', 'available', 'versionDiffers')) + '</tr>'
                   for row in status.get('comparisons', []))
    errors = ''.join('<li>' + html.escape(error['message']) + '</li>' for error in status.get('errors', []))
    last = status.get('lastSuccessfulCheck', {}).get('checkedAt', 'None')
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>' + title + '</title>'
            '<style>body{font:16px system-ui;max-width:960px;margin:40px auto;padding:0 24px;color:#20342c}'
            'table{border-collapse:collapse;width:100%}td,th{padding:12px;border-bottom:1px solid #ddd;text-align:left}'
            '.state{font-size:24px;font-weight:650}code{background:#eef3f0;padding:3px 7px}</style>'
            '<h1>' + title + '</h1><p class="state">' + html.escape(status['status']) + '</p>'
            '<p>Checked: ' + html.escape(status['checkedAt']) + '<br>Last successful check: ' + html.escape(last) + '</p>'
            '<p>\u8fd9\u662f\u53ea\u8bfb\u7248\u672c\u68c0\u67e5\u3002\u68c0\u67e5\u4e0d\u4f1a\u81ea\u52a8\u5b89\u88c5\u6216\u5347\u7ea7\u63d2\u4ef6\u3002</p>'
            '<p>\u8bf7\u6253\u5f00 WorkBuddy \u7684\u6280\u80fd/\u63d2\u4ef6\u9875\u67e5\u770b\u7248\u672c\u53d8\u5316\uff0c\u6309\u516c\u53f8\u5e02\u573a\u7684\u540c\u6b65\u6d41\u7a0b\u66f4\u65b0\u3002</p>' +
            ('<p>Offline/error: the table below is the last successful result, not a fresh comparison.</p>' if status.get('stale') else '') +
            '<ul>' + errors + '</ul><table><tr><th>Plugin</th><th>Installed</th><th>Available</th><th>Version differs</th></tr>' +
            rows + '</table></html>')


def notify_updates(comparisons):
    if os.name != 'nt':
        return False
    script = r'''$ErrorActionPreference='Stop'; Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing;
$notice=New-Object System.Windows.Forms.NotifyIcon;
try {$notice.Icon=[System.Drawing.SystemIcons]::Information; $notice.Visible=$true;
$notice.BalloonTipTitle='FIBOO plugin updates'; $notice.BalloonTipText=$env:FIBOO_UPDATE_NOTICE;
$notice.ShowBalloonTip(6000); Start-Sleep -Seconds 6} finally {$notice.Dispose()}'''
    environment = os.environ.copy()
    lines = [row['plugin'] + ': ' + str(row.get('installed')) + ' -> ' + str(row.get('available'))
             for row in comparisons if row.get('versionDiffers')]
    environment['FIBOO_UPDATE_NOTICE'] = ('\n'.join(lines) + '\nOpen WorkBuddy Skills > Plugins to review.')[:250]
    executable = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
    try:
        result = subprocess.run([executable, '-NoLogo', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden',
                                 '-ExecutionPolicy', 'Bypass', '-Command', script],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env=environment, timeout=10, creationflags=0x08000000)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_check(manager, config, state, source=None, no_head_cache=False, no_notify=False):
    state.mkdir(parents=True, exist_ok=True)
    with exclusive_state(state) as acquired:
        if not acquired:
            return {'status': 'already-running', 'readOnly': True}, 0
        now = datetime.now(timezone.utc).isoformat()
        old = read_state(state / 'update-status.json')
        last = old.get('lastSuccessfulCheck')
        if not isinstance(last, dict):
            last = {}
        current = {'schemaVersion': 1, 'checkedAt': now, 'readOnly': True, 'operation': 'check-updates',
                   'comparisons': last.get('comparisons', []), 'errors': [], 'lastSuccessfulCheck': last,
                   'stale': True, 'success': False, 'notificationsEnabled': not no_notify,
                   'notificationSupported': old.get('notificationSupported'), 'notificationRequested': False,
                   'lastNotifiedSignature': old.get('lastNotifiedSignature')}
        try:
            source = manager.validate_source(source or manager.registered_source(config))
            identity = hashlib.sha256(source.encode('utf-8')).hexdigest()
            is_local = Path(source).is_dir()
            head = None if is_local else remote_head(manager, config, source)
            cached = None
            if not no_head_cache and head and last.get('remoteHead') == head and last.get('sourceIdentity') == identity:
                cached = cached_comparisons(manager, config, last)
            if cached is None:
                result = manager.check_updates(config, source)
                marketplace, comparisons = result['marketplace'], result['comparison']
            else:
                marketplace, comparisons = cached
            success = {'checkedAt': now, 'marketplace': marketplace, 'comparisons': comparisons,
                       'remoteHead': head, 'sourceIdentity': identity,
                       'availableVersions': {row['plugin']: {'version': row.get('available'), 'missing': row.get('missingFromSource', False)}
                                             for row in comparisons},
                       'usedHeadCache': cached is not None}
            count = sum(bool(row.get('versionDiffers')) for row in comparisons)
            current.update(success=True, stale=False, comparisons=comparisons, marketplace=marketplace,
                           updatesAvailable=count, lastSuccessfulCheck=success, usedHeadCache=cached is not None,
                           status='updates-available' if count else 'up-to-date' if comparisons else 'no-installed-plugins')
            if not count:
                current['lastNotifiedSignature'] = None
            elif not no_notify:
                changes = sorted((row['plugin'], str(row.get('installed')), str(row.get('available')))
                                 for row in comparisons if row.get('versionDiffers'))
                signature = hashlib.sha256(json.dumps(changes).encode('utf-8')).hexdigest()
                if signature != old.get('lastNotifiedSignature'):
                    supported = notify_updates(comparisons)
                    current['notificationSupported'] = supported
                    current['notificationRequested'] = supported
                    if supported:
                        current['lastNotifiedSignature'] = signature
            exit_code = 0
        except Exception as error:
            message = str(error) if isinstance(error, manager.MarketError) else 'The check could not finish. Existing plugins and the last successful result were preserved.'
            current.update(status='check-failed', errors=[{'code': 'check-failed', 'message': message}], updatesAvailable=None)
            exit_code = 1
        atomic_write(state / 'update-status.json', json.dumps(current, ensure_ascii=False, indent=2) + '\n')
        atomic_write(state / 'update-report.html', render_report(current))
        return current, exit_code


if __name__ == '__main__':
    os.environ.setdefault('GIT_SSH_COMMAND', 'ssh -oBatchMode=yes -oConnectTimeout=15')
    manager = load_manager(Path(sys.argv[1]))
    result, code = run_check(manager, Path(sys.argv[2]).expanduser().resolve(), Path(sys.argv[3]).expanduser().resolve(),
                             None if sys.argv[4] == '__FIBOO_REGISTERED_SOURCE__' else sys.argv[4],
                             sys.argv[5] == '1', sys.argv[6] == '1')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(code)
# END_PYTHON
'@
$cacheFlag = if ($NoHeadCache) { '1' } else { '0' }
$notifyFlag = if ($NoNotify) { '1' } else { '0' }
$sourceArgument = if ($Source) { $Source } else { '__FIBOO_REGISTERED_SOURCE__' }
$program | & $selected -X utf8 -B - $manager $ConfigDirectory $StateDirectory $sourceArgument $cacheFlag $notifyFlag
exit $LASTEXITCODE
