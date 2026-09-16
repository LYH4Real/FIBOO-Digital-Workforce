"""Top-level install forwarding plus opt-in isolated native/Scheduler acceptance."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / 'scripts/install-market.ps1'
MONITOR_INSTALLER = ROOT / 'scripts/install-update-monitor.ps1'
MARKET_ID = 'fiboo-digital-employee-marketplace'


def powershell(script, *arguments, timeout=30, environment=None):
    return subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                           '-File', str(script), *map(str, arguments)], stdin=subprocess.DEVNULL,
                          capture_output=True, timeout=timeout, env=environment)


def sha_or_none(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


@unittest.skipUnless(os.name == 'nt', 'Windows top-level installer')
class TopLevelInstallTests(unittest.TestCase):
    def stub_install(self, returncode):
        temporary = tempfile.TemporaryDirectory(prefix='fiboo-top-installer-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        scripts = root / 'scripts'
        scripts.mkdir()
        shutil.copyfile(INSTALLER, scripts / 'install-market.ps1')
        manager = r'''param([string]$PythonPath,[string[]]$Arguments)
[ordered]@{pythonPath=$PythonPath;arguments=$Arguments} | ConvertTo-Json | Set-Content -LiteralPath $env:FIBOO_TEST_MANAGER_RESULT -Encoding UTF8
exit __EXIT__
'''.replace('__EXIT__', str(returncode))
        (scripts / 'workbuddy-market.ps1').write_text(manager, encoding='ascii')
        (scripts / 'install-update-monitor.ps1').write_text(r'''param([string]$Source,[string]$ConfigDirectory,[string]$StateDirectory,[string]$TaskName,[string]$PythonPath,[switch]$RunNow)
[ordered]@{source=$Source;configDirectory=$ConfigDirectory;stateDirectory=$StateDirectory;taskName=$TaskName;pythonPath=$PythonPath;runNow=[bool]$RunNow} | ConvertTo-Json | Set-Content -LiteralPath $env:FIBOO_TEST_MONITOR_RESULT -Encoding UTF8
''', encoding='ascii')
        environment = os.environ.copy()
        environment['FIBOO_TEST_MANAGER_RESULT'] = str(root / 'manager.json')
        environment['FIBOO_TEST_MONITOR_RESULT'] = str(root / 'monitor.json')
        result = powershell(scripts / 'install-market.ps1', '-Source', 'https://example.invalid/company.git',
                            '-ConfigDirectory', root / 'profile with spaces', '-StateDirectory', root / 'state with spaces',
                            '-TaskName', 'FIBOO-Test-Forwarding', '-PythonPath', sys.executable, environment=environment)
        return root, result

    def test_successful_child_exit_returns_to_top_level_and_forwards_isolation(self):
        root, result = self.stub_install(0)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        manager = json.loads((root / 'manager.json').read_text(encoding='utf-8-sig'))
        monitor = json.loads((root / 'monitor.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(manager['arguments'], ['--config-dir', str(root / 'profile with spaces'), 'register', '--source', 'https://example.invalid/company.git'])
        self.assertEqual(manager['pythonPath'], sys.executable)
        self.assertEqual(monitor['configDirectory'], str(root / 'profile with spaces'))
        self.assertEqual(monitor['stateDirectory'], str(root / 'state with spaces'))
        self.assertEqual(monitor['taskName'], 'FIBOO-Test-Forwarding')
        self.assertEqual(monitor['pythonPath'], sys.executable)
        self.assertTrue(monitor['runNow'])

    def test_failed_registration_prevents_monitor_install_and_preserves_exit_code(self):
        root, result = self.stub_install(7)
        self.assertEqual(result.returncode, 7)
        self.assertFalse((root / 'monitor.json').exists())

    @unittest.skipUnless(os.environ.get('FIBOO_TEST_MARKET_INSTALL') == '1', 'Opt-in real native registration and Task Scheduler acceptance')
    def test_real_top_level_installer_registers_market_then_runs_monitor(self):
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8]
        evidence = ROOT / '.verification' / ('top-level-install-' + run_id)
        evidence.mkdir(parents=True)
        config, state = evidence / 'isolated-profile', evidence / 'monitor-state'
        task = 'FIBOO-Test-TopInstall-' + uuid.uuid4().hex[:12]
        real_profile = Path.home() / '.workbuddy/plugins'
        real_files = [real_profile / 'known_marketplaces.json', real_profile / 'installed_plugins.json']
        before = {path.name: sha_or_none(path) for path in real_files}
        result_record = {'checkedAt': datetime.now(timezone.utc).isoformat(), 'taskName': task,
                         'source': str(ROOT), 'configDirectory': str(config), 'stateDirectory': str(state)}
        try:
            result = powershell(INSTALLER, '-Source', ROOT, '-ConfigDirectory', config, '-StateDirectory', state,
                                '-TaskName', task, '-PythonPath', sys.executable, timeout=150)
            result_record['installerExitCode'] = result.returncode
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            known = json.loads((config / 'plugins/known_marketplaces.json').read_text(encoding='utf-8-sig'))
            self.assertIn(MARKET_ID, known)
            self.assertTrue(known[MARKET_ID]['autoUpdate'])
            monitor_config = json.loads((state / 'monitor-config.json').read_text(encoding='utf-8-sig'))
            self.assertEqual(Path(monitor_config['configDirectory']).resolve(), config.resolve())
            self.assertEqual(monitor_config['taskName'], task)
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline and not (state / 'update-report.html').exists():
                time.sleep(1)
            self.assertTrue((state / 'update-report.html').is_file(), 'Scheduled monitor did not run after native registration')
            status = json.loads((state / 'update-status.json').read_text(encoding='utf-8'))
            self.assertTrue(status['success'])
            self.assertTrue(status['readOnly'])
            self.assertTrue(status['marketplace']['registered'])
            result_record.update(nativeMarketRegistered=True, nativeAutoUpdateEnabled=True,
                                 scheduledMonitorSucceeded=True, monitorStatus=status['status'],
                                 report=str(state / 'update-report.html'))
        finally:
            removed = powershell(MONITOR_INSTALLER, '-TaskName', task, '-StateDirectory', state, '-Uninstall')
            observed = powershell(MONITOR_INSTALLER, '-TaskName', task, '-StateDirectory', state, '-CheckOnly')
            cleanup = json.loads(observed.stdout.decode('utf-8-sig')) if observed.returncode == 0 else {}
            result_record['testTaskRemoved'] = removed.returncode == 0 and cleanup.get('installed') is False
            result_record['realUserPluginRegistriesUnchanged'] = before == {path.name: sha_or_none(path) for path in real_files}
            (evidence / 'verification-result.json').write_text(json.dumps(result_record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            print('Top-level install evidence: ' + str(evidence))
            self.assertTrue(result_record['testTaskRemoved'], 'Only the isolated test task must be removed')
            self.assertTrue(result_record['realUserPluginRegistriesUnchanged'], 'Real employee plugin registries changed')


if __name__ == '__main__':
    unittest.main()
