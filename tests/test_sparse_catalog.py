"""Verify remote version checks fetch metadata and preserve registry isolation."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('sparse_market', Path(__file__).parents[1] / 'scripts/workbuddy_market.py')
market = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(market)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


class SparseCatalogTests(unittest.TestCase):
    def test_remote_commands_share_proxy_environment_and_one_timeout_budget(self):
        config = Path('profile')
        destination = Path('temporary-market')
        inherited = {'https_proxy': '', 'no_proxy': 'internal.invalid'}
        with mock.patch.object(market, 'discover_git', return_value='git'), \
                mock.patch.object(market, 'git_environment', return_value=inherited) as environment, \
                mock.patch.object(market.time, 'monotonic', side_effect=[100, 101, 111, 121]), \
                mock.patch.object(market.subprocess, 'run', return_value=mock.Mock(returncode=0)) as run:
            market._clone_catalog_metadata(config, 'https://example.invalid/repo.git', destination)
        environment.assert_called_once_with(config)
        calls = run.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertIn('--filter=blob:none', calls[0].args[0])
        self.assertIn('--no-checkout', calls[0].args[0])
        self.assertEqual(calls[1].args[0][1:], ['sparse-checkout', 'set', '--no-cone', '--stdin'])
        patterns = calls[1].kwargs['input'].decode('utf-8')
        for directory in market.METADATA_DIRS:
            self.assertIn('/' + directory + '/marketplace.json\n', patterns)
            self.assertIn('**/' + directory + '/plugin.json\n', patterns)
        self.assertEqual([call.kwargs['timeout'] for call in calls], [119, 109, 99])
        for call in calls:
            self.assertIs(call.kwargs['env'], inherited)
            self.assertEqual(call.kwargs['stderr'], subprocess.DEVNULL)

    def test_failed_or_timed_out_git_stops_without_fallback_full_clone(self):
        for outcome in (mock.Mock(returncode=128), subprocess.TimeoutExpired(['git', 'private-source'], 1)):
            with self.subTest(outcome=type(outcome).__name__), \
                    mock.patch.object(market, 'discover_git', return_value='git'), \
                    mock.patch.object(market, 'git_environment', return_value={}), \
                    mock.patch.object(market.subprocess, 'run', side_effect=outcome if isinstance(outcome, Exception) else None,
                                      return_value=outcome) as run:
                with self.assertRaises(market.MarketError) as caught:
                    market._clone_catalog_metadata(Path('profile'), 'https://example.invalid/repo.git', Path('target'))
                self.assertEqual(run.call_count, 1)
                self.assertNotIn('private-source', str(caught.exception))

    def test_real_git_sparse_checkout_preserves_alternate_metadata_and_excludes_binary_payloads(self):
        with tempfile.TemporaryDirectory(prefix='fiboo-sparse-test-') as temporary:
            root = Path(temporary).resolve()
            source, checkout, config = root / 'source', root / 'checkout', root / 'profile'
            source.mkdir()
            try:
                git = market.discover_git(config)
            except market.MarketError:
                self.skipTest('Git is unavailable')
            def command(*arguments):
                subprocess.run([git, '-C', str(source), *arguments], check=True, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30,
                               creationflags=market.CREATE_NO_WINDOW)
            command('init', '--quiet')
            command('config', 'user.name', 'FIBOO fixture')
            command('config', 'user.email', 'test@example.invalid')
            command('config', 'uploadpack.allowFilter', 'true')
            write_json(source / '.workbuddy-plugin/marketplace.json', {'plugins': [
                {'name': 'nested', 'source': './custom/nested'}, {'name': 'standard', 'source': './plugins/standard'}]})
            write_json(source / 'custom/nested/.claude-plugin/plugin.json', {'name': 'nested', 'version': '1.2.3'})
            write_json(source / 'plugins/standard/.codebuddy-plugin/plugin.json', {'name': 'standard', 'version': '4.5.6'})
            runtime = source / 'custom/nested/runtime'
            runtime.mkdir()
            for name in ('application.exe', 'python.dll'):
                (runtime / name).write_bytes(os.urandom(65536))
            (source / 'README.md').write_text('not required for a version-only check', encoding='utf-8')
            command('add', '.')
            command('commit', '--quiet', '-m', 'Synthetic metadata fixture')
            market._clone_catalog_metadata(config, source.as_uri(), checkout)
            self.assertEqual(market.catalog_versions(checkout), {'nested': '1.2.3', 'standard': '4.5.6'})
            visible = {path.relative_to(checkout).as_posix() for path in checkout.rglob('*')
                       if path.is_file() and '.git' not in path.relative_to(checkout).parts}
            self.assertEqual(visible, {'.workbuddy-plugin/marketplace.json',
                                      'custom/nested/.claude-plugin/plugin.json',
                                      'plugins/standard/.codebuddy-plugin/plugin.json'})
            write_json(checkout / '.workbuddy-plugin/marketplace.json', {'plugins': [{'name': 'escape', 'source': '../outside'}]})
            with self.assertRaisesRegex(market.MarketError, '越出'):
                market.catalog_versions(checkout)


if __name__ == '__main__':
    unittest.main()
