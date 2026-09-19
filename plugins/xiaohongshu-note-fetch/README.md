# 小红书笔记详情

在「fiboo-digital-employee-marketplace」安装 `xiaohongshu-note-fetch`。本插件声明依赖市场中的 `kimi-webbridge`；支持依赖安装的客户端会一并安装。Windows 10/11 x64 自带 MCP 运行环境，无需 Python。浏览器扩展和本地桥接服务仍需按 Kimi 插件的首次使用指引安装、连接。

1. 按 `kimi-webbridge` 插件指引准备 Chrome/Edge、Kimi 扩展和本地桥接服务。
2. 在这个浏览器登录**自己的小红书账号**，保持浏览器打开。
3. 在 WorkBuddy 说：**请先调用 xhs_status，再用 xhs_fetch_note 读取这条完整分享链接的标题、正文、作者和图片地址：【粘贴分享链接】。**

系统需要 Windows 自带的 `curl.exe`。插件不附带浏览器、登录状态、Cookie、真实笔记或账号令牌。

| 工具 | 用途 |
| --- | --- |
| `xhs_status` | 检查桥接和浏览器扩展连接 |
| `xhs_discover_notes` | 打开小红书首页并发现最多 50 条完整链接 |
| `xhs_fetch_note` | 读取一条完整分享链接或分享文案 |
| `xhs_fetch_notes` | 批量读取 1–50 条分享链接 |

请保留分享链接中的原始参数。图片字段返回 URL 和尺寸，不下载图片、不做 OCR、不翻页读取评论。受限、需要登录、已删除或不完整的笔记会返回错误；批量调用要检查每一项 `ok` 和 `error`，不要把部分成功当成全部成功。速度取决于网络、浏览器连接和页面可访问性。

## 更新与排查

安装和更新使用插件相对路径，无需复制 MCP JSON 或重写 EXE 路径。更新后重新加载插件或重启 WorkBuddy。已装旧独立 MCP 的员工若发现重复工具，可在确认插件可用后手动移除旧 `xhs-note-fetcher` 配置。

服务器连接正常只证明 MCP 可运行；`xhs_status` 还需要桥接连接正常。浏览器登录状态由本人浏览器管理，不保存在插件目录，不会随插件更新复制给同事。

离线检查：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1`。它验证 MCP 初始化、4 个工具和中文无效链接错误，不访问小红书、不读取浏览器。需要诊断桥接时可自行运行 `runtime/xhs-note-mcp.exe --check`；这个单独命令会联系本地 Kimi 服务。

仅打包并验证 Windows x64。macOS/Linux 可参考 `source/` 另行构建，不承诺本包可在这些系统直接运行。

## 维护者

`source/` 包含 0.2.0 源码、原分享包源码 wheel 和启动器；`runtime/` 必须完整保留 `_internal/`。`BUILD_INFO.json`、`DEPENDENCIES.json` 与 `licenses/` 来自已核验的原 Windows 分享包。维护者在有 Python、PyInstaller 和 `mcp>=1.20,<2` 的 Windows x64 环境运行 `scripts/build.ps1`；构建后检查、提升插件版本并重新发布。构建脚本不会自动安装依赖。
