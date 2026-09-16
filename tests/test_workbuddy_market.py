import importlib.util
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('workbuddy_market', Path(__file__).parents[1] / 'scripts/workbuddy_market.py')
market = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(market)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


class NativeMarketTests(unittest.TestCase):
    def test_system_proxy_is_process_local_and_no_port_is_assumed(self):
        original = {'PATH': 'kept'}
        with mock.patch.object(market, 'discover_git', return_value='git'), \
             mock.patch.object(market.subprocess, 'run', return_value=mock.Mock(returncode=1)) as run, \
             mock.patch.object(market.urllib.request, 'getproxies', return_value={
                 'http': 'http://proxy.invalid:38421', 'https': 'http://proxy.invalid:48765', 'no': 'intranet.invalid'}):
            result = market.git_environment(Path('profile'), original)
        self.assertEqual(original, {'PATH': 'kept'})
        self.assertEqual(result['https_proxy'], 'http://proxy.invalid:48765')
        self.assertEqual(result['http_proxy'], 'http://proxy.invalid:38421')
        self.assertEqual(result['no_proxy'], 'intranet.invalid')
        self.assertEqual(run.call_args.kwargs['stdout'], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs['stderr'], subprocess.DEVNULL)
        self.assertEqual(run.call_args.args[0][1:3], ['config', '--get-regexp'])

    def test_explicit_proxy_environment_including_disabled_wins(self):
        for name, value in [('HTTPS_PROXY', ''), ('http_proxy', 'http://explicit.invalid'),
                            ('ALL_PROXY', 'socks5://explicit.invalid'), ('NO_PROXY', '*'), ('no_proxy', '')]:
            with self.subTest(name=name, value=value), \
                 mock.patch.object(market.subprocess, 'run') as run, \
                 mock.patch.object(market.urllib.request, 'getproxies') as getproxies:
                result = market.git_environment(Path('profile'), {name: value})
                self.assertEqual(result[name], value)
                self.assertNotIn('https_proxy', result)
                run.assert_not_called()
                getproxies.assert_not_called()

    def test_existing_or_unreadable_git_proxy_config_is_not_overridden(self):
        for returncode in (0, 128):
            with self.subTest(returncode=returncode), \
                 mock.patch.object(market, 'discover_git', return_value='git'), \
                 mock.patch.object(market.subprocess, 'run', return_value=mock.Mock(returncode=returncode)), \
                 mock.patch.object(market.urllib.request, 'getproxies') as getproxies:
                result = market.git_environment(Path('profile'), {})
                self.assertNotIn('https_proxy', result)
                getproxies.assert_not_called()

    def test_real_git_empty_and_scoped_proxy_settings_win(self):
        try:
            market.discover_git(Path('profile'))
        except market.MarketError:
            self.skipTest('Git is unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            environment = {key: value for key, value in os.environ.items()
                           if key.lower() not in ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')}
            environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=str(root / 'gitconfig'))
            for key in ('GIT_CONFIG_COUNT', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_SYSTEM'):
                environment.pop(key, None)
            for config in ('[http]\nproxy =\n', '[http "https://internal.invalid"]\nproxy =\n',
                           '[remote "origin"]\nproxy =\n'):
                (root / 'gitconfig').write_text(config, encoding='utf-8')
                with self.subTest(config=config), mock.patch.object(market.urllib.request, 'getproxies') as proxy:
                    result = market.git_environment(root, environment, cwd=root)
                    proxy.assert_not_called()
                    self.assertNotIn('https_proxy', result)

    def test_native_server_uses_shared_git_environment(self):
        child = mock.Mock()
        child.poll.return_value = None
        inherited = {'https_proxy': 'http://proxy.invalid:27185'}
        with mock.patch.object(market, 'git_environment', return_value=inherited) as environment, \
             mock.patch.object(market, 'WindowsChildJob'), \
             mock.patch.object(market.subprocess, 'Popen', return_value=child) as start, \
             mock.patch.object(market.NativeServer, 'request', return_value=[]):
            with market.NativeServer(Path('cli'), Path('node'), Path('profile')):
                actual = start.call_args.kwargs['env']
                self.assertEqual(actual['https_proxy'], inherited['https_proxy'])
                if os.name == 'nt':
                    self.assertEqual(actual['GIT_CONFIG_KEY_0'], 'core.longpaths')
                    self.assertEqual(actual['GIT_CONFIG_VALUE_0'], 'true')
                environment.assert_called_once()

    def test_native_long_paths_append_without_mutating_existing_git_overrides(self):
        original = {'GIT_CONFIG_COUNT': '2', 'GIT_CONFIG_KEY_0': 'http.version',
                    'GIT_CONFIG_VALUE_0': 'HTTP/1.1', 'GIT_CONFIG_KEY_1': 'url.file:///mirror.insteadOf',
                    'GIT_CONFIG_VALUE_1': 'https://repo.invalid/company.git'}
        result = market.native_git_long_paths(original)
        self.assertEqual(original['GIT_CONFIG_COUNT'], '2')
        self.assertNotIn('GIT_CONFIG_KEY_2', original)
        self.assertEqual(result['GIT_CONFIG_COUNT'], '3')
        self.assertEqual(result['GIT_CONFIG_KEY_2'], 'core.longpaths')
        self.assertEqual(result['GIT_CONFIG_VALUE_2'], 'true')
        self.assertEqual(result['GIT_CONFIG_VALUE_0'], 'HTTP/1.1')
        self.assertEqual(result['GIT_CONFIG_VALUE_1'], original['GIT_CONFIG_VALUE_1'])

    def test_native_long_paths_reject_invalid_override_count(self):
        for value in ('invalid', '-1', '1000000'):
            with self.subTest(value=value), self.assertRaises(market.MarketError):
                market.native_git_long_paths({'GIT_CONFIG_COUNT': value})

    def test_remote_check_clone_uses_shared_git_environment(self):
        inherited = {'https_proxy': 'http://proxy.invalid:27185'}
        with mock.patch.object(market, 'git_environment', return_value=inherited) as environment, \
             mock.patch.object(market, 'discover_git', return_value='git'), \
             mock.patch.object(market.subprocess, 'run', return_value=mock.Mock(returncode=0)) as clone, \
             mock.patch.object(market, 'catalog_versions', return_value={}), \
             mock.patch.object(market, 'installed_status', return_value={'marketplace': {}, 'installed': []}):
            market.check_updates(Path('profile'), 'https://example.invalid/repo.git')
            environment.assert_called_once_with(Path('profile'))
            self.assertIs(clone.call_args.kwargs['env'], inherited)

    def test_new_plugin_names_do_not_need_script_changes(self):
        self.assertEqual(market.parser().parse_args(['install', '--plugin', 'future-plugin-2027']).plugin, 'future-plugin-2027')
        for name in ('other@market', '../escape', 'Bad_Name', '-flag', 'double--hyphen'):
            with self.assertRaises(argparse.ArgumentTypeError):
                market.plugin_name(name)

    def test_check_does_not_mutate_registry_or_start_native(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'profile'
            source = root / 'source'
            registry = config / 'plugins/installed_plugins.json'
            write_json(registry, {'version': 2, 'plugins': {'wave-image@' + market.MARKET_ID: [{'version': '1.0.0', 'scope': 'user'}]}})
            write_json(source / '.codebuddy-plugin/marketplace.json', {'plugins': [{'name': 'wave-image', 'source': './plugins/wave-image'}]})
            write_json(source / 'plugins/wave-image/.codebuddy-plugin/plugin.json', {'name': 'wave-image', 'version': '2.0.0'})
            before = registry.read_bytes()
            with mock.patch.object(market, 'NativeServer', side_effect=AssertionError('must not start')):
                result = market.check_updates(config, str(source))
            self.assertTrue(result['readOnly'])
            self.assertTrue(result['comparison'][0]['versionDiffers'])
            self.assertEqual(before, registry.read_bytes())

    def test_status_does_not_expose_repository_or_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)
            write_json(config / 'plugins/known_marketplaces.json', {market.MARKET_ID: {'manifestName': market.DISPLAY_NAME, 'autoUpdate': True, 'source': {'url': 'https://secret@example.invalid/repo'}}})
            write_json(config / 'settings.json', {'pluginConfigs': {'hidden': {'options': {'secret': 'DONT_PRINT'}}}})
            payload = json.dumps(market.installed_status(config))
            self.assertNotIn('secret', payload)
            self.assertNotIn('DONT_PRINT', payload)
            self.assertTrue(market.installed_status(config)['marketplace']['autoUpdate'])

    def test_reject_inline_credentials_and_option_injection(self):
        for source in ('https://token@example.invalid/repo.git', 'https://example.invalid/repo?token=x', '-c core.sshCommand=x', 'ssh://git:secret@example.invalid/repo'):
            with self.assertRaises(market.MarketError):
                market.validate_source(source)
        self.assertEqual(market.validate_source('git@example.invalid:company/repo.git'), 'git@example.invalid:company/repo.git')

    def test_catalog_rejects_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'source'
            write_json(root / '.codebuddy-plugin/marketplace.json', {'plugins': [{'name': 'bad', 'source': '../outside'}]})
            with self.assertRaises(market.MarketError):
                market.catalog_versions(root)

    def test_shutdown_only_owns_its_child(self):
        server = market.NativeServer(Path('cli'), Path('node'), Path('profile'))
        process = mock.Mock()
        process.poll.return_value = None
        server.process = process
        server.__exit__(None, None, None)
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=8)
        process.kill.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows releases child cwd handles asynchronously')
    def test_cleanup_waits_for_owned_child_directory_handles(self):
        server = market.NativeServer(Path('cli'), Path('node'), Path('profile'))
        server.temp = mock.Mock()
        server.temp.cleanup.side_effect = [PermissionError('child is still releasing cwd'), None]
        with mock.patch.object(market.time, 'sleep') as sleep:
            server.__exit__(None, None, None)
        self.assertEqual(server.temp.cleanup.call_count, 2)
        sleep.assert_called_once_with(0.1)

    @unittest.skipUnless(os.name == 'nt', 'Windows process containment')
    def test_windows_job_closes_own_descendants(self):
        import ctypes
        from ctypes import wintypes
        job = market.WindowsChildJob()
        code = "import sys,subprocess,time;sys.stdin.readline();p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);print(p.pid,flush=True);time.sleep(60)"
        process = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   text=True, creationflags=market.CREATE_NO_WINDOW)
        child_handle = None
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        try:
            job.assign(process)
            process.stdin.write('\n')
            process.stdin.flush()
            child_pid = int(process.stdout.readline())
            child_handle = api.OpenProcess(0x100000, False, child_pid)
            self.assertTrue(child_handle)
            job.close()
            self.assertEqual(api.WaitForSingleObject(child_handle, 5000), 0)
            process.wait(timeout=5)
        finally:
            job.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if child_handle:
                api.CloseHandle(child_handle)
            process.stdin.close()
            process.stdout.close()


if __name__ == '__main__':
    unittest.main()
