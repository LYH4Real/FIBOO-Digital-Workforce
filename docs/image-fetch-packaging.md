# Wave 与小红书读取插件打包记录

本记录对应首次公司市场版本，验证日期：2026-09-16。这里只验证插件包和离线 MCP 协议；WorkBuddy 市场安装、敏感配置界面和自动更新由市场总体验证另行覆盖。

| 插件 | 版本 | 自带运行环境 | 外部前置条件 |
| --- | --- | --- | --- |
| `wave-image` | 0.4.2 | Windows x64 单文件 EXE | 员工本人的 Waveeee API Key；生图时可访问 Waveeee API |
| `xiaohongshu-note-fetch` | 0.2.0 | Windows x64 EXE + `_internal` | `kimi-webbridge` 插件、Kimi 扩展和本地桥接、Chrome/Edge、本人小红书登录、系统 curl |

两个插件都有 `.codebuddy-plugin/plugin.json` 和 `.mcp.json`。MCP 通过 `${CODEBUDDY_PLUGIN_ROOT}` 指向当前插件版本中的程序，不含维护者磁盘路径。`xiaohongshu-note-fetch` 声明同市场依赖 `kimi-webbridge`。Wave 用 `userConfig.api_key` 敏感配置接收密钥并经环境变量传入子进程；市场仓库中不存放密钥。客户端的敏感配置实际存储行为，以员工安装的 WorkBuddy 版本为准。

Wave 图片目录使用 Python 支持的 `~/Pictures/FIBOO/generated_images`，由运行程序展开当前用户目录；不在插件缓存中，插件更新或卸载不删除图片。XHS 登录身份在员工浏览器内，不打包或复制。

官方配置依据：[CodeBuddy 插件参考](https://www.codebuddy.ai/docs/cli/plugins-reference) 中的 MCP 配置、插件根变量、`userConfig` 及插件依赖说明。首次部署仍应以公司实际 WorkBuddy 版本进行安装验收。

## 来源与范围

- Wave 使用现有 0.4.2 EXE，并附带对应 9 个 Python 源文件、项目清单和运行时许可证。
- XHS 从已有可分发 Windows x64 ZIP 按白名单抽取 `runtime/`、`source/`、`licenses/`、构建信息与依赖清单，逐文件核验原 `SHA256SUMS.json`。原 ZIP SHA256 为 `3d722908d7e43a1eff8c17493ece0891490156c19784c6bdc7cbabb5671ee4a6`。
- 包内排除了浏览器档案、Cookie、账号配置、`.venv`、业务图片、真实笔记、benchmark 数据以及旧绝对路径客户端配置。
- 首版可直接运行的二进制仅覆盖 Windows x64；macOS/Linux 尚未构建或验证。

## 已执行验证

1. 每个插件的实际 `.mcp.json` 展开插件根后启动对应 EXE。
2. 将 runtime 复制到包含中文、空格与 `&` 的新目录，从新位置启动；子进程 PATH 仅保留 Windows System32，没有依赖维护者 Python。
3. Wave：初始化成功，准确列出 6 个工具，`server_info` 返回 0.4.2；清空 Key 后正确报告未配置，图片路径位于插件之外。
4. XHS：初始化成功，准确列出 4 个工具；输入中文无效链接，返回 `isError=true` 和 `INVALID_URL`，未联系浏览器。
5. 两个随包 `scripts/check.ps1` 均通过；运行时代码文件的 SHA256 保存在各自 `verification/RUNTIME_SHA256.json`。
6. 对两个 runtime 逐文件复算 SHA256，分别 1 个和 128 个文件与清单完全一致；文件名和可识别密钥字面量检查没有发现账号状态文件或 `sk-` 密钥，包括 XHS 源码 wheel 内部。该扫描不能代替对所有可能密钥格式的全面证明。
7. Wave 的 `save_image_response` 在无 Key 状态保存合成 1 像素 PNG 两次：请求文件名 `contract.jpg`，实际返回 `contract.png`、`contract-2.png`，字节保持一致且旧文件未覆盖。此测试只在临时目录处理内存测试字节，没有网络请求或图片生成调用，测试产物随后清理。
8. planner 0.3.2 包中的预检已适配 `response_format:auto`；真实 Wave EXE 的 `tools/list` 契约测试覆盖 4 个生成/编辑工具、各自省略或使用三种响应格式的共 16 种组合，验证完整预检通过且参数不变。planner 全部 142 个离线回归测试通过。

机器可读结果位于各插件 `verification/offline-protocol.json`。这些结果不证明真实生图、真实笔记读取或员工账号授权；本轮没有发起付费生图，没有抓取真实新数据，没有修改用户 WorkBuddy 配置。

补充证据包括两个插件的 `verification/package-audit.json`，以及 Wave 插件中的 `verification/storage-contract.json`、`verification/planner-contract.json`、`verification/wave-0.4.2-tools.json`。planner 的修复仅位于公司市场插件包，原技能工程未改动。

## 维护步骤

更新维护中的源码后，在隔离的 Windows x64 构建环境运行对应 `scripts/build.ps1`。Wave 需要 Python 3.11+ 和 PyInstaller；XHS 另需与项目清单兼容的 MCP SDK。脚本不主动联网安装依赖。XHS 先自检新构建，再把旧 runtime 备份到 `.build` 后替换，出现构建错误会保留原包。

每次发布需同时更新插件版本、构建信息、依赖/许可证清单、运行时哈希与离线验证记录，再交市场发布流程。`.build/`、本地测试临时目录和业务产物不得进入发布内容。若只是提升公司包装版本，可以保留上游服务版本；验收时分别记录两者。
