"""Validate, version and package the FIBOO WorkBuddy marketplace (stdlib only)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = Path('.codebuddy-plugin/marketplace.json')
MANIFEST = Path('.codebuddy-plugin/plugin.json')
NAME = re.compile(r'^[a-z0-9][a-z0-9-]*$')
SEMVER = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')
EXCLUDED_DIRS = {'.git', '.build', '__pycache__', '.venv', '.pytest_cache'}
ROOT_EXCLUDED_DIRS = {'.verification', 'dist', 'node_modules'}
SENSITIVE_NAMES = {'.env', 'credentials.json', 'auth.json', 'tokens.json', 'identity.json', 'cookies.json', 'local-config.json'}
TEXT_SUFFIXES = {'.json', '.md', '.ps1', '.cmd', '.toml', '.yaml', '.yml', '.py', '.txt',
                 '.js', '.mjs', '.cjs', '.ts', '.ini', '.cfg', '.pem', '.key'}
SECRET_PATTERNS = (
    re.compile(r'\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{30,}\b'),
    re.compile(r'\bsk-[A-Za-z0-9_-]{24,}\b'),
    re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
)


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def files(root: Path):
    """Use the same distribution boundary for plugin hashes and ZIP contents.

    A plugin's dist/node_modules can be its actual executable payload. Likewise,
    a frozen runtime may require pyc files or a standard-library ZIP. Only the
    marketplace-level output/test directories are excluded by those names.
    """
    root = root.resolve()
    marketplace = (root / CATALOG).is_file()
    for directory, dirs, names in os.walk(root, followlinks=False):
        current = Path(directory)
        kept = []
        for name in sorted(dirs):
            path = current / name
            rel = path.relative_to(root)
            runtime = 'runtime' in rel.parts
            if (name == '.git' or
                    (name in EXCLUDED_DIRS and not runtime) or
                    (marketplace and len(rel.parts) == 1 and name in ROOT_EXCLUDED_DIRS)):
                continue
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise ValueError(f'Symlinks/junctions are not distributable: {rel}')
            kept.append(name)
        dirs[:] = kept
        for name in sorted(names):
            path = current / name
            rel = path.relative_to(root)
            if path.is_symlink():
                raise ValueError(f'Symlinks are not distributable: {rel}')
            if path.suffix.lower() in {'.log', '.tmp', '.bak'}:
                continue
            if path.suffix.lower() == '.pyc' and 'runtime' not in rel.parts:
                continue
            if path.is_file():
                yield path


def digest_tree(root: Path) -> str:
    root = root.resolve()
    digest = hashlib.sha256()
    for path in files(root):
        digest.update(path.relative_to(root).as_posix().encode('utf-8') + b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def local_path(base: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative.startswith('./'):
        raise ValueError(f'Expected a ./ relative package path: {relative!r}')
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError(f'Path escapes package: {relative}')
    if not path.exists():
        raise ValueError(f'File/directory does not exist: {relative}')
    return path


def sensitive_filename(name: str) -> bool:
    name = name.lower()
    return (name in SENSITIVE_NAMES or
            (name.startswith('.env.') and name not in {'.env.example', '.env.sample', '.env.template'}) or
            (name.startswith(('credential', 'credentials')) and name.endswith('.json')) or
            bool(re.fullmatch(r'(?:credentials?|auth|tokens?|identity|cookies?)(?:[._-].*)?\.json', name)))


def contains_credential(data) -> bool:
    if isinstance(data, list):
        return any(contains_credential(value) for value in data)
    if not isinstance(data, dict):
        return False
    for key, value in data.items():
        upper = key.upper()
        sensitive = upper in {'API_KEY', 'APIKEY', 'TOKEN', 'SECRET', 'PASSWORD', 'AUTHORIZATION', 'COOKIE'} or upper.endswith(
            ('_API_KEY', '_TOKEN', '_SECRET', '_PASSWORD', '_AUTHORIZATION', '_COOKIE'))
        candidate = value.get('default') if sensitive and isinstance(value, dict) else value if sensitive else None
        if isinstance(candidate, str) and candidate and not re.fullmatch(r'(?:Bearer )?\$\{[^{}]+\}', candidate):
            return True
        if contains_credential(value):
            return True
    return False


def distributable_path(plugin: Path, relative: str, payload: set[Path]) -> Path:
    path = local_path(plugin, relative)
    if (path.is_file() and path not in payload) or (path.is_dir() and not any(item.is_relative_to(path) for item in payload)):
        raise ValueError(f'Referenced path is excluded from the distribution: {plugin.name}/{relative}')
    return path


def validate_mcp(plugin: Path, config: dict, name: str, payload: set[Path]):
    if not isinstance(config, dict):
        raise ValueError(f'MCP config must be an object: {name}')
    servers = config.get('mcpServers', config)
    if not isinstance(servers, dict):
        raise ValueError(f'MCP config must be an object: {name}')
    for server in servers.values():
        if not isinstance(server, dict):
            raise ValueError(f'MCP server must be an object: {name}')
        command = server.get('command', '')
        if not isinstance(command, str) or re.match(r'^(?:[A-Za-z]:[\\/]|/|\\\\)', command):
            raise ValueError(f'Machine-specific/invalid MCP command in {name}')
        arguments = server.get('args', [])
        if not isinstance(arguments, list) or any(not isinstance(item, str) for item in arguments):
            raise ValueError(f'MCP args must be strings: {name}')
        for argument in [command, *arguments]:
            marker = '${CODEBUDDY_PLUGIN_ROOT}/'
            if argument.startswith(marker):
                distributable_path(plugin, './' + argument[len(marker):], payload)
        environment = server.get('env', {})
        if not isinstance(environment, dict):
            raise ValueError(f'MCP env must be an object: {name}')
        for key, value in environment.items():
            if any(s in key.upper() for s in ('API_KEY', 'TOKEN', 'SECRET', 'PASSWORD')):
                if value and not (isinstance(value, str) and re.fullmatch(r'\$\{[^{}]+\}', value)):
                    raise ValueError(f'Inline credential in {name}: {key}')
    if contains_credential(config):
        raise ValueError(f'Inline credential in MCP config: {name}')


def validate_runtime(plugin: Path, name: str):
    lock = plugin / 'verification' / 'RUNTIME_SHA256.json'
    runtime = plugin / 'runtime'
    if not lock.exists():
        if runtime.is_dir() and any(path.is_file() for path in runtime.rglob('*')):
            raise ValueError(f'Runtime lock missing: {name}')
        return
    expected = read_json(lock)
    if not isinstance(expected, dict) or not expected:
        raise ValueError(f'Runtime lock must map paths to SHA256: {name}')
    shipped = {path.relative_to(plugin).as_posix(): path for path in files(plugin)
               if path.is_relative_to(runtime)}
    if set(expected) != set(shipped):
        raise ValueError(f'Runtime lock/file coverage mismatch: {name}')
    for relative, digest in expected.items():
        if (not isinstance(digest, str) or not re.fullmatch(r'[a-fA-F0-9]{64}', digest) or
                hashlib.sha256(shipped[relative].read_bytes()).hexdigest() != digest.lower()):
            raise ValueError(f'Runtime checksum mismatch: {name}/{relative}')


def validate(root: Path = ROOT) -> dict:
    root = root.resolve()
    catalog = read_json(root / CATALOG)
    if catalog.get('name') != 'fiboo-digital-employee-marketplace':
        raise ValueError('The marketplace name must be fiboo-digital-employee-marketplace.')
    if not SEMVER.fullmatch(catalog.get('version', '')):
        raise ValueError('Invalid marketplace version.')
    records, dependencies = {}, {}
    for entry in catalog.get('plugins', []):
        name = entry.get('name', '')
        if not NAME.fullmatch(name) or name in records:
            raise ValueError(f'Invalid or duplicate plugin name: {name}')
        plugin = local_path(root, entry['source'])
        if plugin != (root / 'plugins' / name).resolve():
            raise ValueError(f'Unexpected plugin source for {name}')
        manifest = read_json(plugin / MANIFEST)
        payload = set(files(plugin))
        if manifest.get('name') != name or not SEMVER.fullmatch(manifest.get('version', '')):
            raise ValueError(f'Invalid manifest name/version: {name}')
        if 'version' in entry:
            raise ValueError(f'Keep version only in plugin.json: {name}')
        if not manifest.get('description') or not (plugin / 'README.md').is_file():
            raise ValueError(f'Missing employee description/README: {name}')
        if not (plugin / 'CHANGELOG.md').is_file():
            raise ValueError(f'Missing CHANGELOG: {name}')
        if manifest['version'] not in (plugin / 'CHANGELOG.md').read_text(encoding='utf-8-sig'):
            raise ValueError(f'Current version missing from CHANGELOG: {name}')
        for field in ('skills', 'commands', 'agents', 'hooks', 'mcpServers'):
            value = manifest.get(field)
            if isinstance(value, str):
                distributable_path(plugin, value, payload)
            elif isinstance(value, list):
                for item in value:
                    distributable_path(plugin, item, payload)
        deps = manifest.get('dependencies', [])
        if any(not isinstance(dep, str) for dep in deps):
            raise ValueError(f'Use same-market string dependencies for portable installs: {name}')
        dependencies[name] = deps
        configured_mcp = manifest.get('mcpServers')
        mcp_paths = [configured_mcp] if isinstance(configured_mcp, str) else configured_mcp if isinstance(configured_mcp, list) else []
        if (plugin / '.mcp.json').is_file() and './.mcp.json' not in mcp_paths:
            mcp_paths.append('./.mcp.json')
        for mcp_path in mcp_paths:
            validate_mcp(plugin, read_json(distributable_path(plugin, mcp_path, payload)), name, payload)
        if isinstance(configured_mcp, dict):
            validate_mcp(plugin, configured_mcp, name, payload)
        for skill in plugin.glob('skills/*/SKILL.md'):
            text = skill.read_text(encoding='utf-8-sig')
            if not text.startswith('---\n') and not text.startswith('---\r\n'):
                raise ValueError(f'Skill has no frontmatter: {skill.relative_to(root)}')
            front = text.split('---', 2)[1]
            if not re.search(r'^name:', front, re.M) or not re.search(r'^description:', front, re.M):
                raise ValueError(f'Skill missing name/description: {skill.relative_to(root)}')
        validate_runtime(plugin, name)
        records[name] = {'version': manifest['version'], 'sha256': digest_tree(plugin)}
    for name, deps in dependencies.items():
        for dependency in deps:
            if dependency not in records or dependency == name:
                raise ValueError(f'Missing/recursive dependency: {name} -> {dependency}')
    visited, active = set(), set()
    def visit(name):
        if name in active:
            raise ValueError(f'Cyclic dependency: {name}')
        if name in visited:
            return
        active.add(name)
        for dep in dependencies[name]:
            visit(dep)
        active.remove(name)
        visited.add(name)
    for name in records:
        visit(name)
    for path in files(root):
        if sensitive_filename(path.name):
            raise ValueError(f'Credential/state file must not be shipped: {path.relative_to(root)}')
        rel = path.relative_to(root).as_posix()
        # Bundled third-party runtimes can contain large data files. Inspect only
        # manifests, our scripts and skill instructions, not vendored libraries.
        if path.suffix.lower() in TEXT_SUFFIXES and '/runtime/' not in rel:
            text = path.read_text(encoding='utf-8-sig', errors='replace')
            if any(pattern.search(text) for pattern in SECRET_PATTERNS):
                raise ValueError(f'Possible credential/private key in {rel}; inspect locally (value redacted).')
            if path.suffix.lower() == '.json' and contains_credential(read_json(path)):
                raise ValueError(f'Inline credential in {rel}; inspect locally (value redacted).')
            if path.name != 'release.py' and re.search(r'[CD]:[\\/]+Users[\\/]+Administrator', text, re.I):
                raise ValueError(f'Maintainer-specific path in {rel}')
    if not records:
        raise ValueError('No plugins in marketplace.')
    return {'marketplace': catalog['name'], 'version': catalog['version'], 'plugins': records}


def ensure_new_versions(root: Path, current: dict):
    lock_path = root / 'release-lock.json'
    if not lock_path.exists():
        return
    previous = read_json(lock_path)
    for name, record in current['plugins'].items():
        old = previous.get('plugins', {}).get(name)
        if old and tuple(map(int, record['version'].split('.'))) < tuple(map(int, old['version'].split('.'))):
            raise ValueError(f'{name}: version regression; publish a higher version even when reverting code.')
        if old and old['sha256'] != record['sha256'] and old['version'] == record['version']:
            raise ValueError(f'{name}: content changed without a version bump; clients may keep the old cache.')


def bump(root: Path, name: str, part: str, note: str):
    if not NAME.fullmatch(name):
        raise ValueError('Invalid plugin name.')
    plugin = root / 'plugins' / name
    manifest = read_json(plugin / MANIFEST)
    old = manifest['version']
    if not SEMVER.fullmatch(old):
        raise ValueError('Version is not a stable x.y.z version.')
    nums = list(map(int, old.split('.')))
    index = {'major': 0, 'minor': 1, 'patch': 2}[part]
    nums[index] += 1
    for i in range(index + 1, 3):
        nums[i] = 0
    new = '.'.join(map(str, nums))
    manifest['version'] = new
    write_json(plugin / MANIFEST, manifest)
    changelog = plugin / 'CHANGELOG.md'
    current = changelog.read_text(encoding='utf-8-sig') if changelog.exists() else '# 更新记录\n'
    lines = current.splitlines(keepends=True)
    heading = lines[0] if lines else '# 更新记录\n'
    changelog.write_text(heading + f'\n## {new} — {date.today()}\n\n- {note.strip()}\n\n' + ''.join(lines[1:]).lstrip(), encoding='utf-8')
    return {'plugin': name, 'previousVersion': old, 'version': new}


def new_plugin(root: Path, name: str, description: str):
    if not NAME.fullmatch(name):
        raise ValueError('Use a lowercase kebab-case plugin name.')
    target = root / 'plugins' / name
    if target.exists():
        raise ValueError('Plugin directory already exists.')
    write_json(target / MANIFEST, {'name': name, 'version': '0.1.0', 'description': description, 'author': {'name': 'FIBOO'}, 'skills': './skills/'})
    skill = target / 'skills' / name / 'SKILL.md'
    skill.parent.mkdir(parents=True)
    skill.write_text(f'---\nname: {name}\ndescription: {json.dumps(description, ensure_ascii=False)}\n---\n\n# {description}\n\n此技能刚创建，尚未配置业务流程。被调用时说明当前不可执行，并联系公司维护者完成实现。\n', encoding='utf-8')
    (target / 'README.md').write_text(f'# {description}\n\n开发中的新插件，完成业务实现和验证后再发布。\n', encoding='utf-8')
    (target / 'CHANGELOG.md').write_text(f'# 更新记录\n\n## 0.1.0 — {date.today()}\n\n- 创建插件。\n', encoding='utf-8')
    catalog = read_json(root / CATALOG)
    catalog['plugins'].append({'name': name, 'source': f'./plugins/{name}', 'description': description, 'category': 'productivity'})
    write_json(root / CATALOG, catalog)
    return {'plugin': name, 'path': str(target), 'status': 'draft; implement and verify before release'}


def build(root: Path, output: Path) -> dict:
    root = root.resolve()
    output = output.resolve()
    if output.is_relative_to(root) and not output.is_relative_to(root / 'dist'):
        raise ValueError('Release output must be under the marketplace dist directory or outside the source tree.')
    if output.suffix.lower() != '.zip':
        raise ValueError('Release output must use the .zip extension.')
    checksum_path = output.with_suffix(output.suffix + '.sha256')
    if output.exists() or checksum_path.exists():
        raise ValueError('Output/checksum already exists. Choose a new release file name.')
    current = validate(root)
    ensure_new_versions(root, current)
    output.parent.mkdir(parents=True, exist_ok=True)
    selected = list(files(root))
    lock_text = json.dumps(current, ensure_ascii=False, indent=2) + '\n'
    created_output = created_checksum = False
    temporary_lock = None
    try:
        with output.open('xb') as target:
            created_output = True
            with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                entries = [(path.relative_to(root).as_posix(), path) for path in selected
                           if path.relative_to(root).as_posix() != 'release-lock.json']
                entries.append(('release-lock.json', None))
                for relative, path in sorted(entries):
                    info = zipfile.ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o644 << 16
                    archive.writestr(info, lock_text.encode('utf-8') if path is None else path.read_bytes())
        if validate(root) != current:
            raise ValueError('Source changed while packaging; retry after edits finish.')
        checksum = hashlib.sha256(output.read_bytes()).hexdigest()
        with checksum_path.open('x', encoding='utf-8', newline='\n') as handle:
            created_checksum = True
            handle.write(f'{checksum}  {output.name}\n')
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         dir=root, prefix='.release-lock-', suffix='.tmp', delete=False) as handle:
            temporary_lock = Path(handle.name)
            handle.write(lock_text)
        temporary_lock.replace(root / 'release-lock.json')
        temporary_lock = None
    except BaseException:
        if created_output:
            output.unlink(missing_ok=True)
        if created_checksum:
            checksum_path.unlink(missing_ok=True)
        raise
    finally:
        if temporary_lock is not None:
            temporary_lock.unlink(missing_ok=True)
    return {'archive': str(output), 'sha256': checksum, 'plugins': len(current['plugins'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('validate')
    version = commands.add_parser('bump')
    version.add_argument('plugin')
    version.add_argument('--part', choices=['major', 'minor', 'patch'], default='patch')
    version.add_argument('--note', required=True)
    create = commands.add_parser('new')
    create.add_argument('name')
    create.add_argument('--description', required=True)
    package = commands.add_parser('build')
    package.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.command == 'validate':
            result = validate(root)
            ensure_new_versions(root, result)
        elif args.command == 'bump':
            result = bump(root, args.plugin, args.part, args.note)
        elif args.command == 'new':
            result = new_plugin(root, args.name, args.description)
        else:
            result = build(root, args.output)
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
