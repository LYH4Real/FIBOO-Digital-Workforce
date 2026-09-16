# FIBOO-数字员工市场

供公司员工使用的 WorkBuddy 插件市场。按维护者当前选择，发行仓库暂时公开：[LYH4Real/FIBOO-Digital-Workforce](https://github.com/LYH4Real/FIBOO-Digital-Workforce)。员工首次接入市场，随后在 WorkBuddy 中按需安装插件。

目标环境：Windows 10/11 x64 + WorkBuddy。已核对的宿主为 WorkBuddy 5.5.6 / 随包 CLI 2.137.1。首次连接钉钉、浏览器及图片服务需要员工自己的授权。

## 六个插件

| 插件 | 用途 | 安装时自动带上的依赖 |
|---|---|---|
| Kimi 浏览器助手 `kimi-webbridge` | 浏览器操作 Skill、桥接程序安装与连接检查 | 无；浏览器商店扩展须在浏览器中添加 |
| 钉钉办公 `dingtalk-cli` | 内置 DWS Windows CLI 与钉钉产品技能 | 无 |
| Wave 图片生成 `wave-image` | 文生图、参考图编辑、批量生成 | 无；在 WorkBuddy 中填写个人/公司分配的 Key |
| fiboo 产品资料 `fiboo-product-materials` | 实时查询产品库与提取产品素材 | 钉钉办公 |
| 小红书笔记采集 `xiaohongshu-note-fetch` | 基于员工浏览器登录态获取参考笔记 | Kimi 浏览器助手 |
| 小红书内容策划 `xiaohongshu-content-planner` | 从参考与产品资料到策划、文案、图片 | 以上五个插件 |

## 员工安装

1. 从 [最新发布页](https://github.com/LYH4Real/FIBOO-Digital-Workforce/releases/latest) 下载 `fiboo-marketplace-*.zip` 安装包；当前公开仓库无需员工 GitHub 授权。未来改为私有时再为员工配置仓库读取权限。
2. 解压到本地，双击 **安装市场.cmd**。公司仓库地址已经预填。此操作只注册公司市场并开启更新检测，不会一次安装全部插件。
3. 打开或重新打开 WorkBuddy，在技能页的插件区域找到 **FIBOO-数字员工市场**，按需安装。安装“小红书内容策划”会自动安装它的五项依赖。
4. 告诉 WorkBuddy：“检查我安装的 FIBOO 插件，完成首次配置”。按照[员工使用指南](docs/员工使用指南.md)完成自己的浏览器、钉钉和 Wave 配置。

也可以在 WorkBuddy 的“添加市场”入口直接添加公司 Git 地址，但必须另行开启该市场的自动更新。推荐使用本包安装入口，让这个设置一起完成。

市场显示名为 `FIBOO-数字员工市场`，安装器使用稳定存储标识 `fiboo-digital-employee-marketplace`。员工不要自行更改市场名。

## 更新行为

- 安装入口显式开启本市场 `autoUpdate=true`，并注册当前 Windows 用户的 **FIBOO 更新检查**：每天本地时间 10:00 与用户登录时执行只读版本检查。
- 检查发现已安装插件与市场版本有差异时，尝试发送本机气泡提示并更新 `~/.fiboo/marketplace/update-report.html`。网络不可用时保留上次成功结果；不替换插件、不触碰员工授权。
- WorkBuddy 原生后台更新仍由宿主自己的会话与节流机制决定。已验证开启开关、完整安装与原生同步升级；文件型 Git 模拟测试未证明无人干预的原生升级，所以不把它作为本包自动检测的唯一保证。每日检测机制已用真实 Windows 计划任务验证。
- 执行原生同步时，只更新已安装插件及其依赖。上新插件可在市场中找到，由员工按需安装。
- **检查更新.cmd** 只比较版本，不安装更新。需要立即同步时使用管理脚本 `sync`。新版下载完成后在新任务中使用，必要时重启 WorkBuddy。
- 浏览器商店扩展、员工登录态和业务文件不在插件缓存更新范围。Kimi 插件包含扩展安装入口与桥接版本检查。

## 维护与验证

- [维护者指南](docs/维护者指南.md)：新增插件、版本发布、私有 Git 部署与回退。
- [员工使用指南](docs/员工使用指南.md)：安装、首次授权、检查更新与常见问题。
- [原生机制验证](docs/workbuddy-native-evidence.md)：实际客户端命令、隔离安装与更新证据。
- [自动更新检查](docs/update-monitor.md)：每天与登录时的只读检测、通知、离线行为与卸载。
- [业务技能打包记录](docs/business-skills-packaging.md)、[MCP 打包记录](docs/image-fetch-packaging.md)。

```powershell
python scripts/release.py validate
python -m unittest discover -s tests -v
python scripts/release.py build --output dist/fiboo-marketplace-1.0.0.zip
```

维护脚本也可由 WorkBuddy 自带 Python 执行：`powershell -File scripts/run-python.ps1 scripts/release.py validate`。

## 发布边界

当前按维护者明确选择向上述公开仓库发布。仓库保存插件程序、使用方法与市场清单；不包含维护者的钉钉登录、浏览器 Cookie、Wave Key 或实际任务材料。第三方组件保留各自许可证与来源。公司资料仍由钉钉等服务检查员工身份与权限。
