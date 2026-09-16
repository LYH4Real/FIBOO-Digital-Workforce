import importlib.util
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('release', Path(__file__).parents[1] / 'scripts/release.py')
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fiboo-release-')
        self.root = Path(self.temp.name).resolve()
        release.write_json(self.root / release.CATALOG, {'name': 'FIBOO-数字员工市场', 'version': '1.0.0', 'plugins': []})
        release.new_plugin(self.root, 'example', 'Example skill')

    def tearDown(self):
        self.temp.cleanup()

    def test_version_guard_catches_changed_payload(self):
        release.write_json(self.root / 'release-lock.json', release.validate(self.root))
        (self.root / 'plugins/example/README.md').write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'without a version bump'):
            release.ensure_new_versions(self.root, release.validate(self.root))
        release.bump(self.root, 'example', 'patch', 'Fixed behavior')
        release.ensure_new_versions(self.root, release.validate(self.root))

    def test_dependency_cycle_and_missing_dependency(self):
        path = self.root / 'plugins/example' / release.MANIFEST
        manifest = release.read_json(path)
        manifest['dependencies'] = ['missing']
        release.write_json(path, manifest)
        with self.assertRaisesRegex(ValueError, 'Missing/recursive'):
            release.validate(self.root)
        release.new_plugin(self.root, 'missing', 'Other')
        other = self.root / 'plugins/missing' / release.MANIFEST
        record = release.read_json(other)
        record['dependencies'] = ['example']
        release.write_json(other, record)
        with self.assertRaisesRegex(ValueError, 'Cyclic'):
            release.validate(self.root)

    def test_build_excludes_test_state_and_embeds_lock(self):
        test_state = self.root / '.verification' / 'credentials.json'
        test_state.parent.mkdir()
        test_state.write_text('{}')
        output = self.root / 'dist' / 'market.zip'
        release.build(self.root, output)
        with zipfile.ZipFile(output) as archive:
            self.assertIn('release-lock.json', archive.namelist())
            self.assertNotIn('.verification/credentials.json', archive.namelist())
            self.assertIn('plugins/example/.codebuddy-plugin/plugin.json', archive.namelist())
        self.assertTrue(output.with_suffix('.zip.sha256').exists())

    def test_credentials_fail_closed(self):
        (self.root / 'plugins/example/credentials.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Credential/state'):
            release.validate(self.root)

    def test_source_traversal_rejected(self):
        catalog = release.read_json(self.root / release.CATALOG)
        catalog['plugins'][0]['source'] = './../escape'
        release.write_json(self.root / release.CATALOG, catalog)
        with self.assertRaisesRegex(ValueError, 'escapes'):
            release.validate(self.root)

    def write_runtime(self, name='runtime/server.exe', content=b'synthetic executable'):
        plugin = self.root / 'plugins/example'
        runtime = plugin / name
        runtime.parent.mkdir(parents=True, exist_ok=True)
        runtime.write_bytes(content)
        lock = plugin / 'verification/RUNTIME_SHA256.json'
        hashes = release.read_json(lock) if lock.exists() else {}
        hashes[name] = hashlib.sha256(content).hexdigest()
        release.write_json(lock, hashes)
        return runtime

    def test_runtime_checksums_missing_files_and_unlisted_files(self):
        runtime = self.write_runtime()
        release.validate(self.root)
        runtime.write_bytes(b'corrupt executable')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            release.validate(self.root)
        runtime.unlink()
        with self.assertRaisesRegex(ValueError, 'coverage mismatch'):
            release.validate(self.root)
        self.write_runtime()
        (runtime.parent / 'unlisted.dll').write_bytes(b'untracked dependency')
        with self.assertRaisesRegex(ValueError, 'coverage mismatch'):
            release.validate(self.root)

    def test_frozen_runtime_and_plugin_dependencies_survive_packaging(self):
        self.write_runtime('runtime/_internal/standalone.pyc', b'synthetic pyc')
        self.write_runtime('runtime/_internal/__pycache__/required.pyc', b'required cache')
        library_zip = self.write_runtime('runtime/_internal/base_library.zip', b'')
        with zipfile.ZipFile(library_zip, 'w') as bundle:
            bundle.writestr('encodings/__init__.pyc', b'compiled stdlib module')
        self.write_runtime('runtime/_internal/base_library.zip', library_zip.read_bytes())
        plugin = self.root / 'plugins/example'
        for relative in ('node_modules/example/index.js', 'dist/server.js'):
            target = plugin / relative
            target.parent.mkdir(parents=True)
            target.write_text('runtime entry', encoding='utf-8')
        output = self.root / 'dist/complete.zip'
        release.build(self.root, output)
        with zipfile.ZipFile(output) as bundle:
            for relative in ('runtime/_internal/standalone.pyc',
                             'runtime/_internal/__pycache__/required.pyc',
                             'runtime/_internal/base_library.zip',
                             'node_modules/example/index.js', 'dist/server.js'):
                self.assertEqual(bundle.read('plugins/example/' + relative), (plugin / relative).read_bytes())

    def test_build_and_test_state_do_not_change_payload_or_get_shipped(self):
        baseline = release.validate(self.root)
        for relative in ('.verification/credentials.json', '.build/old-runtime/credentials.json',
                         'plugins/example/.build/rebuild/credentials.json',
                         'plugins/example/scripts/__pycache__/compiled.pyc'):
            path = self.root / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(b'local testing only')
        self.assertEqual(release.validate(self.root), baseline)
        output = self.root / 'dist/clean.zip'
        release.build(self.root, output)
        with zipfile.ZipFile(output) as bundle:
            self.assertFalse(any('.verification/' in name or '.build/' in name or '__pycache__/' in name
                                 for name in bundle.namelist()))

    def test_credential_file_variants_and_embedded_private_keys_are_rejected(self):
        plugin = self.root / 'plugins/example'
        for name in ('.env.production', '.env.local', 'credentials.backup.json', 'credentials2026.json',
                     'credential-employee.json', 'tokens_personal.json', 'cookies.session.json'):
            with self.subTest(name=name):
                path = plugin / name
                path.write_text('{}', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'Credential/state'):
                    release.validate(self.root)
                path.unlink()
        path = plugin / 'settings.txt'
        path.write_text('-----BEGIN ' + 'PRIVATE KEY-----', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'value redacted'):
            release.validate(self.root)

    def test_declared_mcp_config_and_inline_manifest_credentials_are_checked(self):
        plugin = self.root / 'plugins/example'
        manifest_path = plugin / release.MANIFEST
        manifest = release.read_json(manifest_path)
        config = {'service': {'command': 'python', 'args': [], 'env': {'SERVICE_API_KEY': 'synthetic-secret'}}}
        manifest['mcpServers'] = './mcp/services.json'
        release.write_json(manifest_path, manifest)
        release.write_json(plugin / 'mcp/services.json', config)
        with self.assertRaisesRegex(ValueError, 'Inline credential'):
            release.validate(self.root)
        manifest['mcpServers'] = config
        release.write_json(manifest_path, manifest)
        with self.assertRaisesRegex(ValueError, 'Inline credential'):
            release.validate(self.root)
        config['service']['env']['SERVICE_API_KEY'] = '${user_config.api_key}'
        release.write_json(plugin / 'mcp/services.json', config)
        release.write_json(manifest_path, manifest)
        release.validate(self.root)

    def test_unreferenced_json_config_cannot_smuggle_credentials(self):
        config = self.root / 'plugins/example/old-settings.json'
        release.write_json(config, {'env': {'WAVEEEE_API_KEY': 'synthetic-secret'}})
        with self.assertRaisesRegex(ValueError, 'Inline credential'):
            release.validate(self.root)

    def test_mcp_executable_cannot_reference_excluded_build_or_cache_file(self):
        plugin = self.root / 'plugins/example'
        target = plugin / '.build/server.py'
        target.parent.mkdir()
        target.write_text('print("not shipped")', encoding='utf-8')
        release.write_json(plugin / '.mcp.json', {'service': {
            'command': 'python', 'args': ['${CODEBUDDY_PLUGIN_ROOT}/.build/server.py']}})
        with self.assertRaisesRegex(ValueError, 'excluded from the distribution'):
            release.validate(self.root)

    def test_missing_runtime_lock_and_damaged_archive_inputs_fail_before_build(self):
        path = self.root / 'plugins/example/runtime/server.exe'
        path.parent.mkdir()
        path.write_bytes(b'synthetic runtime')
        with self.assertRaisesRegex(ValueError, 'Runtime lock missing'):
            release.build(self.root, self.root / 'dist/fail.zip')
        self.assertFalse((self.root / 'dist/fail.zip').exists())

    def test_version_rollback_cannot_bypass_cache_guard(self):
        release.bump(self.root, 'example', 'minor', 'New behavior')
        release.write_json(self.root / 'release-lock.json', release.validate(self.root))
        manifest_path = self.root / 'plugins/example' / release.MANIFEST
        manifest = release.read_json(manifest_path)
        manifest['version'] = '0.1.0'
        release.write_json(manifest_path, manifest)
        with self.assertRaisesRegex(ValueError, 'version regression'):
            release.ensure_new_versions(self.root, release.validate(self.root))

    def test_repeated_zip_builds_are_deterministic_and_do_not_embed_old_archives(self):
        first, second = self.root / 'dist/first.zip', self.root / 'dist/second.zip'
        release.build(self.root, first)
        release.build(self.root, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with zipfile.ZipFile(second) as bundle:
            self.assertFalse(any(name.startswith('dist/') for name in bundle.namelist()))
            lock = json.loads(bundle.read('release-lock.json'))
            self.assertEqual(lock, release.read_json(self.root / 'release-lock.json'))

    def test_build_rejects_archive_inside_source_or_existing_checksum(self):
        for relative in ('market.zip', 'plugins/example/release.zip', 'releases/market.zip'):
            with self.subTest(relative=relative):
                with self.assertRaisesRegex(ValueError, 'dist directory or outside'):
                    release.build(self.root, self.root / relative)
        output = self.root / 'dist/new.zip'
        checksum = output.with_suffix('.zip.sha256')
        checksum.parent.mkdir()
        checksum.write_text('do not overwrite', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            release.build(self.root, output)
        self.assertEqual(checksum.read_text(encoding='utf-8'), 'do not overwrite')

    def test_failed_build_keeps_previous_lock_and_cleans_partial_output(self):
        lock = self.root / 'release-lock.json'
        previous = release.validate(self.root)
        release.write_json(lock, previous)
        release.bump(self.root, 'example', 'patch', 'Change')
        output = self.root / 'dist/failed.zip'
        with patch.object(zipfile.ZipFile, 'writestr', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                release.build(self.root, output)
        self.assertEqual(release.read_json(lock), previous)
        self.assertFalse(output.exists())
        self.assertFalse(output.with_suffix('.zip.sha256').exists())

    def test_build_into_external_directory_and_unicode_filename(self):
        with tempfile.TemporaryDirectory(prefix='fiboo-export-') as destination:
            output = Path(destination) / '数字员工市场.zip'
            result = release.build(self.root, output)
            self.assertEqual(result['sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertIn(output.name, output.with_suffix('.zip.sha256').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
