# WorkBuddy 原生市场兼容性与验收证据

验证日期：2026-09-16。Windows 本机 WorkBuddy 5.5.6，应用随包 CodeBuddy CLI 2.137.1，随包 Node 实际输出 v22.22.2（分发目录 `22.22.2-3`），随包 Python 实际输出 **3.13.14**。Python 默认环境与 `versions/3.13.12/python.exe` 均输出 3.13.14，因此不能依据版本目录名判断运行版本。以下结果限定于该版本；升级 WorkBuddy 后应复验原生接口。

## 已通过的验收

| 能力 | 实际结果 |
| --- | --- |
| 市场展示名 | manifest 使用 ASCII 稳定标识 `fiboo-digital-employee-marketplace`；WorkBuddy 会拒绝第三方市场的中文 manifest 名称 |
| 稳定安装标识 | 注册别名使用 `fiboo-digital-employee-marketplace`；安装目标为 `插件名@别名` |
| 原生依赖安装 | 只安装 mock planner，原生自动安装声明的依赖并把依赖记录为 `auto: true` |
| 自动更新开关 | 通过原生 API 写入 `autoUpdate: true`，读取原生状态复核通过 |
| 只读检查 | 发布 mock v2 后，`check-updates` 比较出 v1/v2 差异，注册表文件字节不变，不启动原生 runtime |
| 显式同步升级 | `sync` 后 mock planner 及其依赖均由 1.0.0 升级到 2.0.0，原生注册表记录新版本 |
| 服务认证 | 无认证访问返回 401；Bearer 认证访问成功；自动更新开关接口返回 204 |
| Windows 进程清理 | 私有 Job Object 关闭后，仅本次服务及后代退出；真实父/子孙进程回归通过 |
| 六包整体加载 | 另一隔离验收中，仅安装真实 planner 后得到 6 插件、19 skills、1 MCP；reload errors 为 0，临时目录退出时成功删除 |

原始隔离证据位于 `.verification/native-probe/`：`results.jsonl`、`serve-results.json`、`integration-results.json`、`chinese-id-results.json`。六包验收位于 `.verification/full-native/final-pass/native-result.json`。这些本机探针不属于需要发布的插件内容。

## 展示名称与存储别名必须区分

市场文件 `.codebuddy-plugin/marketplace.json`：

```json
{
  "name": "fiboo-digital-employee-marketplace",
  "owner": {"name": "FIBOO"},
  "plugins": [
    {"name": "example-plugin", "source": "./plugins/example-plugin"}
  ]
}
```

添加时通过 `name` 参数指定英文存储别名。返回值使用英文 `id` 与 manifest 的 `name`。桌面 `mapCliMarketplace` 保留这两个字段，分类标签读取 `marketplace.name`。不需要未获原生支持的 `displayName` 扩展。

**第三方市场 manifest 名称必须使用 ASCII。** WorkBuddy 5.5.6 的 `isBlockedOfficialName` 会拒绝含非 ASCII 字符的第三方市场名，并拒绝仿官方名称。GUI 仅提交市场源时会回退使用 manifest 的 `name`，因此 `FIBOO-数字员工市场` 会被拒绝。`fiboo-digital-employee-marketplace` 已用隔离原生注册和浏览验证；中文品牌说明保留在 description、README 和员工文档中。该名称同时是稳定的注册别名，不能改动它，否则已安装插件的 `插件名@别名` 标识会变化。

## 原生管理器命令

Windows 入口自动寻找 WorkBuddy 随包 Python、Node 和 CLI，不要求员工安装独立 CLI。可用 `-PythonPath` 覆盖 Python；Python 的全局参数必须写在子命令前。

```powershell
.\scripts\workbuddy-market.ps1 register --source https://github.com/LYH4Real/FIBOO-Digital-Workforce.git
.\scripts\workbuddy-market.ps1 status
.\scripts\workbuddy-market.ps1 install --plugin xiaohongshu-content-planner
.\scripts\workbuddy-market.ps1 check-updates
.\scripts\workbuddy-market.ps1 sync
```

隔离验证用法：

```powershell
.\scripts\workbuddy-market.ps1 --config-dir .\.verification\my-profile register --source .
.\scripts\workbuddy-market.ps1 --config-dir .\.verification\my-profile install --plugin xiaohongshu-content-planner
```

可另外传入 `--cli <cli/bin/codebuddy>` 与 `--node <node.exe>`。默认配置目录是当前用户的 `~/.workbuddy`。`status` 和 `check-updates` 只读取版本信息；Git 检查使用临时 partial clone 和 sparse checkout，仅取得市场与插件版本清单，不修改原生缓存和配置。输出不包含仓库凭据、插件配置值或认证令牌。

2026-09-16，公司真实 HTTPS 仓库的稀疏检测耗时 8.042 秒：工作树仅 7 个 JSON、4,045 字节，Git 对象 44,799 字节；没有下载 EXE/DLL，六项版本比较正确，隔离注册表哈希未改变。证据在本机 `.verification/sparse-catalog-20260916T100333Z/result.json`。

`install` 接受安全的小写 kebab-case 名称，由原生市场验证插件是否存在；上新插件不需要更新管理脚本的固定名单。

## 临时原生服务契约

服务由管理器创建，仅监听 loopback，随机端口，每次生成独立密码，不输出服务 stdout/stderr：

```text
node <WorkBuddy随包cli/bin/codebuddy> --serve --host 127.0.0.1 --port <随机端口>
  --auth password --setting-sources user --no-session-persistence
```

关键环境：

```text
CODEBUDDY_CONFIG_DIR=<目标用户目录>
WORKBUDDY_CONFIG_DIR=<同一目标用户目录>
CODEBUDDY_FORCE_HEADLESS_BUNDLE=1
CODEBUDDY_GATEWAY_AUTH=password
CODEBUDDY_GATEWAY_PASSWORD=<本次随机值>
GIT_TERMINAL_PROMPT=0
GCM_INTERACTIVE=Never
```

两项 config-dir 同时设置，防止插件目录与模型/用户目录分离。测试时必须使用隔离目录。请求头使用 `Authorization: Bearer <本次随机值>`、`X-CodeBuddy-Request: 1`、`Content-Type: application/json`。

Git 默认不会读取 Windows 的系统代理。管理器与定时检测共用 `git_environment`：在没有显式代理环境变量、也没有 Git 的 `http.proxy`、按 URL 指定的代理或 remote 代理配置时，为本次子进程临时继承系统代理。显式空值禁用同样优先；不写用户 Git 配置，不输出代理地址或凭据。2026-09-16 在真实用户上下文对官方 `https://github.com/git/git.git` 完成 HTTPS `ls-remote HEAD` 只读验证，自动继承代理后成功，且输出匹配完整的 40～64 位十六进制哈希及 HEAD 标识，空输出不会通过。这只验证代理连接能力，不证明公司仓库已发布。受限测试上下文读取不到该用户的系统代理，因此网络验收必须区分上下文。

| 方法与接口 | 请求体 / 用途 |
| --- | --- |
| `GET /api/v1/plugins/marketplaces` | 市场列表 |
| `POST /api/v1/plugins/marketplaces` | `{source, name: "fiboo-digital-employee-marketplace", autoUpdate: true}` |
| `POST /api/v1/plugins/marketplaces/auto-update` | `{marketplace: "fiboo-digital-employee-marketplace", autoUpdate: true}` |
| `POST /api/v1/plugins/marketplaces/browse` | `{marketplace: "fiboo-digital-employee-marketplace"}` |
| `POST /api/v1/plugins` | `{plugin: "插件名@fiboo-digital-employee-marketplace", scope: "user"}` |
| `POST /api/v1/plugins/marketplaces/update` | `{marketplace: "fiboo-digital-employee-marketplace"}` |
| `POST /api/v1/plugins/update` | `{plugin: "插件名@fiboo-digital-employee-marketplace", scope: "user", waitForApply: true}` |

`sync` 先同步原生市场，再通过原生接口更新该市场已安装的 user-scope 插件。仅调用市场 update 对目录来源不一定推进已安装版本，因此不能拿市场刷新成功代替插件升级结果。

管理器不手写 `known_marketplaces.json` 或 `installed_plugins.json`。修改全部经原生 API，继续使用原生的注册表与市场互斥锁。Windows 私有 Job Object 限定本次启动的进程树；退出时清理本次 MCP 等后代，不连接或终止已运行的 WorkBuddy。现有任务可能仍持有旧版本投影，新任务或重启后再使用更新结果。

## 原生状态与依赖格式

当前原生 `plugins/installed_plugins.json` 的顶层版本为 2；`plugins` 映射的值是按 scope 区分的记录数组，例如：

```json
{
  "version": 2,
  "plugins": {
    "example-plugin@fiboo-digital-employee-marketplace": [
      {"scope": "user", "version": "1.0.0", "installPath": "<原生缓存路径>", "auto": true}
    ]
  }
}
```

同市场的插件 manifest 使用真实原生依赖：

```json
{
  "name": "example-planner",
  "version": "1.0.0",
  "dependencies": ["example-helper"]
}
```

字符串依赖由同一市场安装，不要求 Git tag。带版本范围的依赖对象也受支持，但 Git 来源的版本解析可能要求 `插件名--v版本号` tags；不要把字符串依赖和版本范围依赖混用后仍假设免 tag。

## userConfig 的实际行为

原生插件配置可使用：

```json
{
  "userConfig": {
    "api_key": {"description": "服务 API Key", "sensitive": true}
  }
}
```

MCP 环境变量使用 `${user_config.api_key}`。缺值会产生 `MissingPluginUserConfigError`，需员工在 WorkBuddy 中补齐。还支持 `${PLUGIN_OPT:api_key}` 与相应的 `CODEBUDDY_PLUGIN_OPTION_API_KEY` 导出。

**当前版本不要宣称密钥一定保存在操作系统钥匙串。** 实际随包 `PluginOptionsStorageImpl` 把敏感字段放在 `<config-dir>/credentials.json` 的 `pluginSecrets` 下；桌面也读取该文件。市场包不包含这些值，管理器也不读取或打印它们。

## 原生自动升级的验证边界

原生源码确有自动维护机制：runtime 的 `startup` / `prewarm-activate` 完成插件投影后进入后台维护，维护阶段完成后延迟 5 秒执行 `autoUpdateMarketplaces()`。该函数筛选 `autoUpdate === true` 且超过间隔的市场。默认间隔是 24 小时，`CODEBUDDY_MARKETPLACE_AUTO_UPDATE_INTERVAL_MS` 接受大于 0 的数值；0 会回退默认值。

本次隔离探针使用本地裸 Git 仓库的 `file://` 地址模拟远端，先安装 v1，再推送 v2；市场开关为 true，间隔设为 1ms，明确移除 `DISABLE_AUTOUPDATER` 与 `FORCE_AUTOUPDATE_PLUGINS`。ACP `initialize`、`session/new` 成功；另一次执行纯本地 `/status`，没有发起模型请求。等待 35 秒后版本和 `lastUpdated` 均未前进。

最后一次诊断仅在内存中插入日志，没有更改原始程序或控制流程，记录确认：`startup`、settings settled、manager ready、全部后台维护阶段与 `automatic check` 均已执行。因此已证明原生自动检查代码会运行，**尚未证明该测试来源能自动推进已安装版本**。原因未定，不能据此断言 HTTPS 来源也失败，不能把手动 `sync` 当作自动升级通过证据。原始结果是 `auto-update-results.json`，该探针以失败状态退出是如实记录未满足断言。

本项目的可验收保证是独立的只读更新检测与原生 `sync` 升级；原生开关保持开启。若需要进一步宣称“新版发布后 WorkBuddy 自行完成升级”，应针对真实 HTTPS 市场做独立 v1→v2 发布验收，观察原生注册表、安装版本与下一任务投影，不能只观察 autoUpdate 标识。

## 官方参考与复验

- [WorkBuddy 插件](https://www.workbuddy.cn/docs/workbuddy/Plugins)
- [WorkBuddy 插件功能说明](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Plug-In)
- [CLI 插件市场](https://www.workbuddy.cn/docs/cli/plugin-marketplaces)
- [CLI 环境变量](https://www.workbuddy.cn/docs/cli/env-vars)

管理器测试：`python -m unittest discover -s tests -p test_workbuddy_market.py -v`。本机 13 项通过，包括只读检查、秘密不输出、来源和插件名校验、路径越界拒绝、只清理自建进程、Windows 后代清理、系统代理临时继承以及显式代理/禁用优先级。
