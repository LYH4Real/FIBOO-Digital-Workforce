# 六插件原生安装验收

2026-09-16，在 Windows x64、WorkBuddy 随包 CLI **2.137.1** 上完成。使用项目 `.verification/full-native/final-pass` 全新隔离配置；未向当前员工的 WorkBuddy 配置注册市场或安装插件。

当前市场显示名称和内部 ID 均为 **fiboo-digital-employee-marketplace**。原 v1.0.1 验收使用的中文名在最新 WorkBuddy 添加市场界面中被判为保留名称；v1.0.2 改用兼容的 ASCII 稳定标识。本次来源为本地市场目录，验收安装与加载能力；真实 HTTPS Git 发布、安装及原生自动升级另行验收，只读定时检测结果见 [自动更新检查](update-monitor.md)。

仅发出一次安装请求：`xiaohongshu-content-planner`。原生安装器自动安装其余五个依赖，`plugin list --json`、原生市场 browse 与已安装接口均返回六个插件。

| 插件 | 版本 | 安装方式 | 技能数 |
| --- | --- | --- | --- |
| kimi-webbridge | 2.0.5 | 自动依赖 | 2 |
| dingtalk-cli | 1.0.61 | 自动依赖 | 15 |
| wave-image | 0.4.2 | 自动依赖 | 0 |
| fiboo-product-materials | 0.1.0 | 自动依赖 | 1 |
| xiaohongshu-note-fetch | 0.2.0 | 自动依赖 | 0 |
| xiaohongshu-content-planner | 0.3.2 | 主动安装 | 1 |

全部六个插件原生 manifest 校验通过。原生 reload 返回：6 个插件、19 个技能、0 个禁用插件、1 个 MCP 配置、0 个错误。缓存中的 19 份技能均有有效的 name/description frontmatter，数量与原生加载结果一致。冻结后的源包与安装缓存共 **601 个文件逐字节一致**，包括源码、技能、启动配置和二进制。

**Wave Key 未填写。** 原生 browse 返回 `userConfig.api_key.sensitive=true`，没有默认值；隔离配置没有 credentials.json 或插件选项值。随包实现对缺失 `${user_config.api_key}` 的配置会抛出 `MissingPluginUserConfigError`，因此不能把插件 enabled 状态当作生图已就绪。本次未测试桌面密钥表单交互，也未配置示例 Key 冒充员工凭据。

原生市场 `autoUpdate=true` 已保存；此字段证明更新开关开启，不能单独证明远程升级已发生。Kimi 扩展、本地桥接、钉钉授权、小红书登录和 Wave Key 仍需员工首次使用时完成。未发起生图、抓取真实笔记或发送业务消息。

首次 reload 验收发现管理服务退出时临时目录被占用，随后管理器加入 Windows Job Object。最终重试确认服务及所拥有进程退出，临时目录删除成功，未再发生清理异常。

机器可读证据均保存在 `.verification/full-native/final-pass/`：

- `native-result.json`：原生注册、安装、browse、列表、reload 与退出清理结果。
- `cli-list.json`：随包 CLI `plugin list --json` 的六个原始安装记录。
- `native-validate-all.json`：六插件原生校验结果。
- `summary.json`：601 文件身份核对、技能名称、缺 Key 配置证据及验证边界。

## 顶层安装器闭环

2026-09-16 09:30 UTC，使用真实本地市场源、全新独立 WorkBuddy 配置及随机 FIBOO 计划任务，执行完整 `scripts/install-market.ps1`。原生注册后继续安装监测器并自动运行，3 项测试全部通过，总耗时 9.981 秒：

- 顶层安装退出码为 0；原生市场已注册，`autoUpdate=true`。
- `ConfigDirectory`、`StateDirectory`、`TaskName`、`PythonPath` 正确传到两个子脚本；`workbuddy-market.ps1` 的 `exit 0` 返回后，顶层确实继续安装监测器。另测得注册子脚本 `exit 7` 会阻止监测安装并保留退出码。
- 随机计划任务启动后生成成功的只读 JSON/HTML 报告。此全新配置尚未安装插件，结果为 `no-installed-plugins`；具体版本差异检测另由监测器的合成安装测试验证。
- 测试任务已删除，测试报告保留。当前真实员工的市场和插件注册表在测试前后的哈希完全一致。

测试在获授权的系统执行环境运行，以允许 Task Scheduler/CIM 操作；没有注册真实员工的长期任务。可选隔离参数不改变常规安装的默认目录和任务名。

证据目录：`.verification/top-level-install-20260916T093040Z-b0aaba05/`，包含 `verification-result.json`、`monitor-state/update-status.json` 和可打开的 `monitor-state/update-report.html`。测试命令为设置 `FIBOO_TEST_MARKET_INSTALL=1` 后执行 `python -B -m unittest discover -s tests -p "test_install_market.py" -v`；默认测试不会执行真实注册与计划任务验收。

## 公开 HTTPS 仓库验收

发布后，从 `https://github.com/LYH4Real/FIBOO-Digital-Workforce.git` 注册全新隔离市场，仅安装内容策划插件，原生装齐六个插件。远端基线提交为 `358cc040e6e4bb932f7d3e48cce84086e0cf9609`；本轮 Wave 插件包版本为 0.4.2。市场缓存的 Git origin、提交和文件树均与该公开仓库匹配。

真实 HTTPS 验收结果为六插件、19 技能、reload errors=0、autoUpdate=true。只读更新检查得到六项相同版本，插件注册表字节不变。远端缓存中的六个插件摘要与 release-lock 一致；Windows 超长验收目录采用扩展路径做文件审计，完整校验通过。当前员工真实配置的四个相关文件在验收前后均未改变。

验收定位并修复了 Windows Git 在原生暂存目录中遇到长路径的问题：管理器仅为本次原生服务的 Git 子进程设置 `core.longpaths=true`，保留已有参数，不改系统或全局 Git 配置。Windows Job 关闭后等待文件句柄释放再清理临时目录，独立退出清理验收通过。

证据位于本机 `.verification/https-native/completion-v1.json`。v1.0.1 随包包含这些安装器修复，以及 Wave 自检的无 BOM UTF-8 修复；Wave 包版本递增为 0.4.3，图片服务运行时仍为 0.4.2。
