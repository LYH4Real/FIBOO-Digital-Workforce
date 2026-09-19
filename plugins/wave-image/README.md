# Wave 图片生成与编辑

在「fiboo-digital-employee-marketplace」安装并启用 `wave-image`，按客户端提示输入公司为你分配的 Waveeee API Key。Windows 10/11 x64 自带运行环境，无需安装 Python。

第一次使用可说：**请调用 Wave 的 server_info，确认版本为 0.4.2，且 api_key_configured 为 true。** 此检查不产生生图请求。之后即可按需生成、编辑图片；生成和编辑会调用 Waveeee 服务，使用所配置账号的额度。

| 工具 | 用途 |
| --- | --- |
| `generate_image` | 单次生成 1–4 张图片 |
| `edit_image` | 以 1–10 张本地图片作参考编辑 |
| `generate_batch_images` | 批量生成，每批最多 100 项 |
| `edit_batch_images` | 批量参考编辑，每批最多 100 项 |
| `save_image_response` | 保存已有接口响应的图片，不重新生成 |
| `server_info` | 查看版本、模型、配置是否就绪 |

图片默认保存在当前用户的 `Pictures/FIBOO/generated_images` 文件夹，工具会返回实际绝对路径。这个目录位于插件之外，插件更新和卸载不会清除其中的作品。插件中不包含 API Key、历史图片或同事账号。

默认模型是 `gpt-image-2.5`。在发起付费请求前先确认 prompt、参考图和尺寸；上游模型和画面质量仍以实际返回为准。常用竖版尺寸 `1248x1664` 通过当前程序的本地尺寸校验。请始终使用工具返回的图片路径与扩展名。

## 更新与排查

通过公司市场获取新版本，重新加载插件或重启 WorkBuddy 后调用 `server_info` 确认运行版本。无需手工修改 EXE 路径。

若启用时没有出现密钥输入框，或工具提示未配置 API Key，请先更新 WorkBuddy 至支持插件 `userConfig` 的版本，并在插件设置中配置敏感字段 `api_key`。不要把密钥写入本插件文件。仅支持 Windows x64；本包没有宣称 macOS/Linux 已可直接安装。

离线验证（在插件目录运行）：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1`。验证只初始化 MCP、列出工具并读取服务信息，不联网生图。

## 维护者

`source/` 是 0.4.2 源码，`runtime/` 是相应 Windows EXE，`licenses/` 保留运行环境许可证。修改后需在 Windows x64 Python 3.11+ 构建环境运行 `scripts/build.ps1`，提升插件版本、检查协议，再发布市场版本。构建脚本不会自动安装构建依赖。
