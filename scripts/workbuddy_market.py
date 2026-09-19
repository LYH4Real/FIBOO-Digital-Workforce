#!/usr/bin/env python3
"""Manage FIBOO through WorkBuddy's bundled, authenticated native plugin API.

No credentials are accepted in a repository URL. Git uses the employee's existing
SSH agent or credential manager. Status/check-updates never start the runtime.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

MARKET_ID = "fiboo-digital-employee-marketplace"
DISPLAY_NAME = "fiboo-digital-employee-marketplace"
METADATA_DIRS = (".codebuddy-plugin", ".workbuddy-plugin", ".claude-plugin")
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class MarketError(Exception):
    pass


def read_json(path: Path, default=None):
    if not path.exists() and default is not None:
        return default
    if path.stat().st_size > 8_000_000:
        raise MarketError(f"配置文件过大：{path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise MarketError(f"JSON 格式无效：{path.name}") from exc


def installed_status(config_dir: Path):
    known = read_json(config_dir / "plugins/known_marketplaces.json", {})
    row = known.get(MARKET_ID)
    registry = read_json(config_dir / "plugins/installed_plugins.json", {"plugins": {}})
    settings = read_json(config_dir / "settings.json", {})
    enabled = settings.get("enabledPlugins", {})
    installed = []
    for plugin_id, records in registry.get("plugins", {}).items():
        if not plugin_id.endswith("@" + MARKET_ID):
            continue
        if isinstance(records, dict):
            records = [records]
        for record in records:
            installed.append({"plugin": plugin_id.split("@", 1)[0],
                              "version": record.get("version"),
                              "scope": record.get("scope", "user"),
                              "enabled": enabled.get(plugin_id),
                              "autoInstalled": record.get("auto", record.get("autoInstalled", False))})
    return {"marketplace": {"id": MARKET_ID, "name": (row or {}).get("manifestName", DISPLAY_NAME),
                            "registered": row is not None,
                            "autoUpdate": bool((row or {}).get("autoUpdate", False)),
                            "lastUpdated": (row or {}).get("lastUpdated")},
            "installed": sorted(installed, key=lambda item: (item["plugin"], item["scope"]))}


def validate_source(source: str) -> str:
    candidate = Path(source).expanduser()
    if candidate.is_dir():
        return str(candidate.resolve())
    if source.startswith("-") or any(ch in source for ch in ("\n", "\r", "\x00")):
        raise MarketError("市场来源格式无效。")
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme in ("https", "ssh"):
        if not parsed.hostname or parsed.query or parsed.fragment or parsed.password:
            raise MarketError("Git 地址不能包含密码、查询参数或片段；请使用凭据管理器或 SSH。")
        if parsed.scheme == "https" and parsed.username:
            raise MarketError("HTTPS Git 地址不能内嵌用户名或令牌；请使用凭据管理器。")
        return source
    if re.fullmatch(r"[\w.-]+@[\w.-]+:[\w./-]+", source):
        return source
    raise MarketError("请提供本地市场目录、HTTPS 私有 Git 地址或 SSH Git 地址。")


def _registry_install_roots():
    if os.name != "nt":
        return []
    import winreg
    roots = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for location in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
            try:
                with winreg.OpenKey(hive, location) as parent:
                    for index in range(winreg.QueryInfoKey(parent)[0]):
                        try:
                            with winreg.OpenKey(parent, winreg.EnumKey(parent, index)) as key:
                                display = str(winreg.QueryValueEx(key, "DisplayName")[0])
                                if "workbuddy" not in display.lower():
                                    continue
                                for value_name in ("InstallLocation", "DisplayIcon", "UninstallString"):
                                    try:
                                        value = str(winreg.QueryValueEx(key, value_name)[0]).strip()
                                        match = re.match(r'^"([^"]+)"', value)
                                        value = match.group(1) if match else re.sub(r",\s*\d+$", "", value)
                                        p = Path(value)
                                        roots.append(p.parent if p.suffix.lower() == ".exe" else p)
                                    except OSError:
                                        pass
                        except OSError:
                            pass
            except OSError:
                pass
    return roots


def discover_cli(explicit: str | None = None) -> Path:
    if explicit:
        value = Path(explicit).expanduser().resolve()
        if value.is_file():
            return value
        raise MarketError("--cli 指定的随包 CLI 不存在。")
    roots = _registry_install_roots()
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles"),
                 os.environ.get("ProgramFiles(x86)")):
        if base:
            roots.extend(Path(base) / suffix for suffix in ("WorkBuddy", "Programs/WorkBuddy", "Tencent/WorkBuddy"))
    if os.environ.get("WORKBUDDY_APP_PATH"):
        p = Path(os.environ["WORKBUDDY_APP_PATH"])
        roots.extend((p, p.parent.parent))
    executable = shutil.which("WorkBuddy.exe")
    if executable:
        roots.append(Path(executable).parent)
    # Bounded directory lookup also covers custom locations such as D:\workbuddy.
    if os.name == "nt":
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = Path(letter + ":/")
            if drive.exists():
                roots.extend((drive / "WorkBuddy", drive / "Program Files/WorkBuddy"))
    for root in dict.fromkeys(roots):
        for suffix in ("resources/app.asar.unpacked/cli/bin/codebuddy", "cli/bin/codebuddy", "bin/codebuddy"):
            candidate = root / suffix
            if candidate.is_file():
                return candidate.resolve()
    raise MarketError("未找到 WorkBuddy 随包 CLI。请先安装 WorkBuddy，或使用 --cli 指定 bin/codebuddy。")


def discover_node(explicit: str | None, config_dir: Path) -> Path:
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if candidate.is_file():
            return candidate
        raise MarketError("--node 指定的 Node 不存在。")
    for base in dict.fromkeys((config_dir, Path.home() / ".workbuddy")):
        candidates = list((base / "binaries/node").glob("versions/*/node.exe"))
        if candidates:
            return max(candidates, key=lambda p: p.stat().st_mtime).resolve()
    found = shutil.which("node")
    if found:
        return Path(found).resolve()
    raise MarketError("未找到 WorkBuddy 随包 Node。请先启动 WorkBuddy，或使用 --node 指定。")


class WindowsChildJob:
    """Windows owns this private process tree; closing it cannot affect other apps."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("perProcessUserTime", ctypes.c_int64), ("perJobUserTime", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("minWorkingSet", ctypes.c_size_t),
                        ("maxWorkingSet", ctypes.c_size_t), ("activeProcessLimit", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("reads", "writes", "other", "readBytes", "writeBytes", "otherBytes")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IoCounters),
                        ("processMemoryLimit", ctypes.c_size_t), ("jobMemoryLimit", ctypes.c_size_t),
                        ("peakProcessMemory", ctypes.c_size_t), ("peakJobMemory", ctypes.c_size_t)]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise MarketError("无法创建临时服务的 Windows 进程容器。")
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise MarketError("无法设置临时服务的进程清理规则。")

    def assign(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise MarketError("无法隔离临时服务的子进程；服务已停止。")

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class NativeServer:
    """Owns only its child process; never attaches to/kills an existing desktop."""
    def __init__(self, cli: Path, node: Path, config_dir: Path, timeout: float = 90):
        self.cli, self.node, self.config_dir, self.timeout = cli, node, config_dir, timeout
        self.process = None
        self.password = secrets.token_urlsafe(36)
        self.temp = None
        self.job = None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, method: str, route: str, payload=None, timeout=120):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.base + route, data=body, method=method,
                                     headers={"Authorization": "Bearer " + self.password,
                                              "X-CodeBuddy-Request": "1", "Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=timeout) as response:
                raw = response.read()
                value = json.loads(raw) if raw else None
                return value.get("data", value) if isinstance(value, dict) else value
        except urllib.error.HTTPError as exc:
            # Native errors can contain complete source URLs/configuration. Never echo them.
            raise MarketError(f"WorkBuddy 原生接口失败（HTTP {exc.code}，{route}）。请检查仓库权限、市场及插件名称。") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MarketError("WorkBuddy 原生接口连接失败或超时。") from exc

    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fiboo-workbuddy-native-")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update({"CODEBUDDY_CONFIG_DIR": str(self.config_dir), "WORKBUDDY_CONFIG_DIR": str(self.config_dir),
                    "CODEBUDDY_SKIP_BUILTIN_MARKETPLACE": "1", "CODEBUDDY_DISABLE_COMPILE_CACHE": "1",
                    "CODEBUDDY_FORCE_HEADLESS_BUNDLE": "1",
                    "DISABLE_TELEMETRY": "1", "DISABLE_AUTOUPDATER": "1", "CODEBUDDY_SKIP_GIT_BASH_CHECK": "1",
                    "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never",
                    "CODEBUDDY_GATEWAY_AUTH": "password", "CODEBUDDY_GATEWAY_PASSWORD": self.password})
        env.pop("FORCE_AUTOUPDATE_PLUGINS", None)
        env = git_environment(self.config_dir, env, cwd=self.temp.name)
        if os.name == "nt":
            env = native_git_long_paths(env)
        try:
            if os.name == "nt":
                self.job = WindowsChildJob()
            self.process = subprocess.Popen([str(self.node), str(self.cli), "--serve", "--host", "127.0.0.1",
                                             "--port", str(port), "--auth", "password", "--setting-sources", "user",
                                             "--no-session-persistence"], cwd=self.temp.name, env=env,
                                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
            if self.job is not None:
                self.job.assign(self.process)
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise MarketError("WorkBuddy 原生管理服务启动失败；请检查随包 CLI 与 Node 版本。")
                try:
                    self.request("GET", "/api/v1/plugins/marketplaces", timeout=1)
                    return self
                except MarketError:
                    time.sleep(0.25)
            raise MarketError("WorkBuddy 原生管理服务启动超时。")
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *unused):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
        if self.job is not None:
            self.job.close()
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.temp is not None:
            # Closing a Windows Job initiates termination; descendant processes
            # can briefly retain their working-directory handles after it returns.
            for attempt in range(50):
                try:
                    self.temp.cleanup()
                    break
                except PermissionError:
                    if os.name != "nt" or attempt == 49:
                        raise
                    time.sleep(0.1)


def registered_source(config_dir: Path):
    known = read_json(config_dir / "plugins/known_marketplaces.json", {})
    row = known.get(MARKET_ID)
    if not row:
        raise MarketError("尚未注册 FIBOO 市场；请先运行 register --source。")
    source = row.get("source", {})
    if isinstance(source, str):
        return source
    if source.get("source") == "github" and source.get("repo"):
        return "https://github.com/" + source["repo"] + ".git"
    return source.get("url") or source.get("path") or row.get("installLocation")


def _find_manifest(root: Path, filename: str):
    for folder in METADATA_DIRS:
        candidate = root / folder / filename
        if candidate.is_file():
            return candidate
    raise MarketError(f"市场或插件缺少 {filename}。")


def catalog_versions(root: Path):
    manifest = read_json(_find_manifest(root, "marketplace.json"))
    versions = {}
    for entry in manifest.get("plugins", []):
        name = entry.get("name")
        source = entry.get("source")
        if not isinstance(source, str):
            versions[name] = entry.get("version")
            continue
        plugin_root = (root / source).resolve()
        if not plugin_root.is_relative_to(root.resolve()):
            raise MarketError("插件路径越出市场目录。")
        plugin = read_json(_find_manifest(plugin_root, "plugin.json"))
        versions[name] = plugin.get("version") or entry.get("version")
    return versions


def discover_git(config_dir: Path):
    found = shutil.which("git")
    if found:
        return found
    for base in dict.fromkeys((config_dir, Path.home() / ".workbuddy")):
        binaries = base / "binaries/PortableGit"
        candidates = list(binaries.glob("**/cmd/git.exe"))
        if candidates:
            return str(max(candidates, key=lambda p: p.stat().st_mtime))
    raise MarketError("检查私有 Git 更新需要 Git；请先启动 WorkBuddy 完成随包工具初始化。")


def git_environment(config_dir: Path, environment=None, *, cwd=None):
    """Fill missing Git proxy settings from the OS for this child process only.

    Empty proxy environment variables and empty Git proxy values are deliberate
    opt-outs. Never inspect or log proxy values from Git configuration, and never
    write a persistent Git setting. If config cannot be inspected, leave it alone.
    """
    env = dict(os.environ if environment is None else environment)
    env.update({"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"})
    proxy_names = {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
    if any(name.lower() in proxy_names for name in env):
        return env
    try:
        configured = subprocess.run(
            [discover_git(config_dir), "config", "--get-regexp", r"^(http(\..*)?\.proxy|remote\..*\.proxy)$"],
            cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=5, creationflags=CREATE_NO_WINDOW)
        # 0 includes explicitly empty values; 1 alone means no matching setting.
        if configured.returncode != 1:
            return env
        proxies = urllib.request.getproxies()
    except (MarketError, OSError, subprocess.TimeoutExpired):
        return env
    for scheme, variable in (("http", "http_proxy"), ("https", "https_proxy"),
                             ("all", "all_proxy"), ("no", "no_proxy")):
        value = proxies.get(scheme)
        if isinstance(value, str) and value and not any(ch in value for ch in ("\r", "\n", "\x00")):
            env[variable] = value
    return env


def native_git_long_paths(environment):
    """Allow native Windows staging paths without changing any Git config file."""
    env = dict(environment)
    try:
        index = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError as exc:
        raise MarketError("现有 GIT_CONFIG_COUNT 无效；无法设置原生服务的临时 Git 参数。") from exc
    if not 0 <= index <= 4096:
        raise MarketError("现有 GIT_CONFIG_COUNT 超出允许范围。")
    env.update({"GIT_CONFIG_COUNT": str(index + 1),
                f"GIT_CONFIG_KEY_{index}": "core.longpaths",
                f"GIT_CONFIG_VALUE_{index}": "true"})
    return env


def _clone_catalog_metadata(config_dir: Path, source: str, destination: Path):
    """Read remote versions without materializing plugin runtime payloads.

    Non-cone patterns include every supported plugin metadata directory at any
    depth, so catalog-defined local paths continue to work without hardcoded
    plugin names. GitHub's blob:none support also avoids downloading executable
    blobs into Git's object store, not just excluding them from the worktree.
    """
    git = discover_git(config_dir)
    environment = git_environment(config_dir)
    deadline = time.monotonic() + 120
    patterns = "".join(f"/{directory}/marketplace.json\n**/{directory}/plugin.json\n"
                       for directory in METADATA_DIRS).encode("utf-8")
    commands = [
        ([git, "clone", "--filter=blob:none", "--no-checkout", "--depth", "1", "--single-branch",
          "--no-tags", "--", source, str(destination)], None, None),
        ([git, "sparse-checkout", "set", "--no-cone", "--stdin"], destination, patterns),
        ([git, "checkout", "--force"], destination, None),
    ]
    for command, directory, input_bytes in commands:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MarketError("只读拉取市场目录超时；已安装插件没有变化。")
        options = {"cwd": directory, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                   "env": environment, "timeout": remaining, "creationflags": CREATE_NO_WINDOW}
        if input_bytes is None:
            options["stdin"] = subprocess.DEVNULL
        else:
            options["input"] = input_bytes
        try:
            result = subprocess.run(command, **options)
        except subprocess.TimeoutExpired as exc:
            raise MarketError("只读拉取市场目录超时；已安装插件没有变化。") from exc
        if result.returncode:
            raise MarketError("只读拉取市场目录失败；请检查 Git、网络或仓库权限。已安装插件没有变化。")


def check_updates(config_dir: Path, source: str | None = None):
    source = validate_source(source or registered_source(config_dir))
    with tempfile.TemporaryDirectory(prefix="fiboo-catalog-check-") as tmp:
        root = Path(source)
        if not root.is_dir():
            root = Path(tmp) / "market"
            _clone_catalog_metadata(config_dir, source, root)
        versions = catalog_versions(root)
    result = installed_status(config_dir)
    result["operation"] = "check-updates"
    result["readOnly"] = True
    result["comparison"] = [{"plugin": entry["plugin"], "installed": entry["version"],
                              "available": versions.get(entry["plugin"]),
                              "versionDiffers": entry["plugin"] in versions and versions[entry["plugin"]] != entry["version"],
                              "missingFromSource": entry["plugin"] not in versions}
                             for entry in result.pop("installed")]
    return result


def plugin_name(value: str):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
        raise argparse.ArgumentTypeError("插件名须为小写字母、数字及连接号，例如 new-plugin。")
    return value


def parser():
    p = argparse.ArgumentParser(description="FIBOO WorkBuddy 原生市场管理器")
    p.add_argument("--cli", help="WorkBuddy 随包 cli/bin/codebuddy 路径")
    p.add_argument("--node", help="随包 node.exe 路径")
    p.add_argument("--config-dir", default=str(Path.home() / ".workbuddy"), help="WorkBuddy 配置目录；测试请指定隔离目录")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("register", help="注册市场并开启自动更新").add_argument("--source", required=True)
    sub.add_parser("status", help="只读查看已注册市场及已安装版本")
    sub.add_parser("install", help="原生安装插件及声明的依赖").add_argument("--plugin", type=plugin_name, required=True)
    sub.add_parser("check-updates", help="只读比较源版本，绝不升级").add_argument("--source")
    sub.add_parser("sync", help="原生同步市场并升级已安装插件")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    config_dir = Path(args.config_dir).expanduser().resolve()
    try:
        if args.command == "status":
            result = installed_status(config_dir)
        elif args.command == "check-updates":
            result = check_updates(config_dir, args.source)
        else:
            source = validate_source(args.source) if args.command == "register" else None
            if args.command != "register" and not installed_status(config_dir)["marketplace"]["registered"]:
                raise MarketError("请先运行 register --source 注册公司市场。")
            with NativeServer(discover_cli(args.cli), discover_node(args.node, config_dir), config_dir) as api:
                if args.command == "register":
                    api.request("POST", "/api/v1/plugins/marketplaces", {"source": source, "name": MARKET_ID, "autoUpdate": True})
                    api.request("POST", "/api/v1/plugins/marketplaces/auto-update", {"marketplace": MARKET_ID, "autoUpdate": True})
                elif args.command == "install":
                    api.request("POST", "/api/v1/plugins", {"plugin": args.plugin + "@" + MARKET_ID, "scope": "user"})
                elif args.command == "sync":
                    api.request("POST", "/api/v1/plugins/marketplaces/update", {"marketplace": MARKET_ID})
                    # Directory marketplaces report no changed remote snapshot. Explicit
                    # native updates also make sync reliable before a first session.
                    for installed in installed_status(config_dir)["installed"]:
                        if installed["scope"] != "user":
                            continue
                        api.request("POST", "/api/v1/plugins/update",
                                    {"plugin": installed["plugin"] + "@" + MARKET_ID,
                                     "scope": "user", "waitForApply": True})
                result = installed_status(config_dir)
                result["operation"] = args.command
                result["message"] = "已通过 WorkBuddy 原生接口完成。正在进行的任务可能继续使用旧版本；请在下一新任务中使用，必要时重启 WorkBuddy。"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (MarketError, OSError) as exc:
        # Do not print command lines, native stdout, repository URLs, or gateway secrets.
        message = str(exc) if isinstance(exc, MarketError) else "本地文件或进程操作失败；请检查安装目录和权限。"
        print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
