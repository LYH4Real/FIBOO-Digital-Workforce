"""Read-only monitor tests. Task Scheduler integration is explicitly opt-in."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/update-monitor.ps1'
INSTALLER = ROOT / 'scripts/install-update-monitor.ps1'
PROGRAM = SCRIPT.read_text(encoding='utf-8-sig').split('# BEGIN_PYTHON\n', 1)[1].split('# END_PYTHON', 1)[0]
monitor = types.ModuleType('fiboo_update_monitor_test')
exec(compile(PROGRAM, str(SCRIPT), 'exec'), monitor.__dict__)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fiboo-monitor-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config = self.root / 'profile'
        self.source = self.root / 'source'
        self.state = self.root / 'state'
        self.manager = monitor.load_manager(ROOT / 'scripts/workbuddy_market.py')
        self.plugin_id = 'wave-image@' + self.manager.MARKET_ID
        self.registry = self.config / 'plugins/installed_plugins.json'
        write_json(self.registry, {'version': 2, 'plugins': {self.plugin_id: [{'version': '1.0.0', 'scope': 'user'}]}})
        write_json(self.config / 'plugins/known_marketplaces.json', {self.manager.MARKET_ID: {'autoUpdate': True, 'source': {'path': str(self.source)}}})
        write_json(self.config / 'settings.json', {'enabledPlugins': {self.plugin_id: True}})
        write_json(self.source / '.codebuddy-plugin/marketplace.json', {'plugins': [{'name': 'wave-image', 'source': './plugins/wave-image'}]})
        self.manifest = self.source / 'plugins/wave-image/.codebuddy-plugin/plugin.json'
        write_json(self.manifest, {'name': 'wave-image', 'version': '2.0.0'})

    def check(self, **options):
        return monitor.run_check(self.manager, self.config, self.state, str(self.source), **options)

    def test_real_local_comparison_is_readonly_and_writes_usable_report(self):
        before = {path: path.read_bytes() for path in self.config.rglob('*') if path.is_file()}
        with mock.patch.object(self.manager, 'NativeServer', side_effect=AssertionError('never start native')):
            result, code = self.check(no_notify=True)
        self.assertEqual(code, 0)
        self.assertTrue(result['comparisons'][0]['versionDiffers'])
        self.assertTrue(result['success'])
        self.assertEqual(result, json.loads((self.state / 'update-status.json').read_text(encoding='utf-8')))
        self.assertIn('wave-image', (self.state / 'update-report.html').read_text(encoding='utf-8'))
        self.assertEqual(before, {path: path.read_bytes() for path in self.config.rglob('*') if path.is_file()})

    def test_offline_retains_last_success_and_never_exposes_raw_exception(self):
        first, _ = self.check(no_notify=True)
        with mock.patch.object(self.manager, 'check_updates', side_effect=OSError('private-secret-must-not-leak')):
            failed, code = self.check(no_notify=True)
        self.assertEqual(code, 1)
        self.assertFalse(failed['success'])
        self.assertTrue(failed['stale'])
        self.assertEqual(first['lastSuccessfulCheck'], failed['lastSuccessfulCheck'])
        self.assertEqual(first['comparisons'], failed['comparisons'])
        self.assertTrue(failed['errors'])
        self.assertNotIn('private-secret', json.dumps(failed))

    def test_remote_head_uses_shared_git_environment_and_preserves_explicit_settings(self):
        before = os.environ.copy()
        cases = [
            {'HTTPS_PROXY': 'http://proxy.example.invalid:8080', 'NO_PROXY': 'internal.example.invalid'},
            {'HTTPS_PROXY': '', 'NO_PROXY': '', 'GIT_SSH_COMMAND': 'custom-ssh -oBatchMode=yes'},
        ]
        for configured in cases:
            with self.subTest(configured=configured):
                response = subprocess.CompletedProcess(['git'], 0, (('AB' * 20) + '\tHEAD\n').encode('ascii'))
                with mock.patch.object(self.manager, 'git_environment', return_value=configured.copy(), create=True) as shared, \
                        mock.patch.object(self.manager, 'discover_git', return_value='bundled-git.exe'), \
                        mock.patch.object(monitor.subprocess, 'run', return_value=response) as run:
                    head = monitor.remote_head(self.manager, self.config, 'https://example.invalid/fiboo.git')
                shared.assert_called_once_with(self.config)
                self.assertEqual(head, 'ab' * 20)
                sent = run.call_args.kwargs['env']
                for key, value in configured.items():
                    self.assertEqual(sent[key], value)
                self.assertEqual(sent['GIT_TERMINAL_PROMPT'], '0')
                self.assertEqual(sent['GCM_INTERACTIVE'], 'Never')
                self.assertIn('BatchMode=yes', sent['GIT_SSH_COMMAND'])
                self.assertEqual(run.call_args.kwargs['stderr'], subprocess.DEVNULL)
                self.assertEqual(run.call_args.kwargs['timeout'], 30)
        self.assertEqual(dict(os.environ), before)

    def test_unchanged_head_avoids_clone_but_rechecks_local_installed_versions(self):
        original = self.manager.check_updates
        response = original(self.config, str(self.source))
        with mock.patch.object(monitor, 'remote_head', return_value='a' * 40), \
                mock.patch.object(self.manager, 'check_updates', return_value=response) as fetch:
            first, _ = monitor.run_check(self.manager, self.config, self.state, 'https://example.invalid/fiboo.git', no_notify=True)
            write_json(self.registry, {'plugins': {self.plugin_id: [{'version': '2.0.0', 'scope': 'user'}]}})
            second, _ = monitor.run_check(self.manager, self.config, self.state, 'https://example.invalid/fiboo.git', no_notify=True)
        self.assertFalse(first['usedHeadCache'])
        self.assertTrue(second['usedHeadCache'])
        self.assertFalse(second['comparisons'][0]['versionDiffers'])
        self.assertEqual(fetch.call_count, 1)

    def test_newly_installed_plugin_or_changed_head_requires_fresh_catalog(self):
        response = self.manager.check_updates(self.config, str(self.source))
        with mock.patch.object(monitor, 'remote_head', return_value='b' * 40) as head, \
                mock.patch.object(self.manager, 'check_updates', return_value=response) as fetch:
            monitor.run_check(self.manager, self.config, self.state, 'https://example.invalid/fiboo.git', no_notify=True)
            registry = json.loads(self.registry.read_text())
            registry['plugins']['fiboo-product-materials@' + self.manager.MARKET_ID] = [{'version': '1.0.0'}]
            write_json(self.registry, registry)
            monitor.run_check(self.manager, self.config, self.state, 'https://example.invalid/fiboo.git', no_notify=True)
            self.assertEqual(fetch.call_count, 2)
            head.return_value = 'c' * 40
            monitor.run_check(self.manager, self.config, self.state, 'https://example.invalid/fiboo.git', no_notify=True)
            self.assertEqual(fetch.call_count, 3)

    def test_notifications_only_on_changed_update_set_and_failure_is_nonfatal(self):
        with mock.patch.object(monitor, 'notify_updates', return_value=True) as notify:
            first, _ = self.check()
            second, _ = self.check()
            self.assertTrue(first['notificationRequested'])
            self.assertFalse(second['notificationRequested'])
            self.assertEqual(notify.call_count, 1)
            write_json(self.manifest, {'name': 'wave-image', 'version': '3.0.0'})
            third, _ = self.check()
            self.assertTrue(third['notificationRequested'])
            self.assertEqual(notify.call_count, 2)
            notify.return_value = False
            write_json(self.manifest, {'name': 'wave-image', 'version': '4.0.0'})
            unsupported, code = self.check()
        self.assertEqual(code, 0)
        self.assertTrue(unsupported['success'])
        self.assertFalse(unsupported['notificationSupported'])

    def test_html_escapes_data_and_corrupt_old_state_does_not_block_check(self):
        self.state.mkdir()
        (self.state / 'update-status.json').write_text('{broken', encoding='utf-8')
        result, code = self.check(no_notify=True)
        self.assertEqual(code, 0)
        result['comparisons'][0]['plugin'] = '<script>alert(1)</script>'
        rendered = monitor.render_report(result)
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;', rendered)

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell entrypoint')
    def test_powershell_entrypoint_works_with_spaces_and_without_system_python(self):
        state = self.root / 'state with spaces'
        command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(SCRIPT),
                   '-Source', str(self.source), '-ConfigDirectory', str(self.config), '-StateDirectory', str(state),
                   '-PythonPath', sys.executable, '-NoNotify']
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        saved = json.loads((state / 'update-status.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['updatesAvailable'], 1)

    @unittest.skipUnless(os.name == 'nt' and os.environ.get('FIBOO_TEST_SCHEDULED_TASKS') == '1', 'Opt-in isolated Task Scheduler integration')
    def test_isolated_scheduled_task_registers_runs_and_uninstalls(self):
        task = 'FIBOO-Test-' + uuid.uuid4().hex[:12]
        base = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(INSTALLER),
                '-TaskName', task, '-StateDirectory', str(self.state)]
        try:
            result = subprocess.run(base + ['-Source', str(self.source), '-ConfigDirectory', str(self.config),
                                    '-PythonPath', sys.executable, '-NoNotify', '-RunNow'], capture_output=True, timeout=45)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline and not (self.state / 'update-status.json').is_file():
                time.sleep(1)
            self.assertTrue((self.state / 'update-status.json').is_file(), 'The isolated scheduled task did not create its status report')
            saved = json.loads((self.state / 'update-status.json').read_text(encoding='utf-8'))
            self.assertTrue(saved['success'])
            self.assertEqual(saved['updatesAvailable'], 1)
            self.assertTrue((self.state / 'scripts/install-update-monitor.ps1').is_file())
        finally:
            removed = subprocess.run(base + ['-Uninstall'], capture_output=True, timeout=30)
            self.assertEqual(removed.returncode, 0, removed.stderr.decode(errors='replace'))


if __name__ == '__main__':
    unittest.main()
