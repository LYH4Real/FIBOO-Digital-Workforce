# FIBOO 产品资料

版本：0.1.0。按名称查询公司钉钉产品资料表，获取规格、卖点、Slogan、PDF 和主图，并保留记录 ID、来源字段、文档页码及真实文件路径。

## 安装与首次使用

1. 从“fiboo-digital-employee-marketplace”安装本插件；其依赖 `dingtalk-cli` 由市场依赖声明一并安装。
2. 按 `dingtalk-cli` 的初始化说明准备 DWS，使用员工本人的钉钉身份登录，并确认有公司产品资料库读取权限。
3. 准备 Python 3.10 或更新版本。PDF 正文提取另需 `skills/fiboo-product-materials/requirements-pdf.txt` 声明的 `pypdf`；可装入任务专用虚拟环境。
4. 在 WorkBuddy 中提出“查找 fiboo 某产品的资料与主图”。只做资料检索时，无需生图服务。

按 `dingtalk-cli` 的 `scripts/setup.ps1` 指引，将 DWS 放入当前用户的 `.fiboo/bin`，当前进程若尚未刷新 PATH，重开 WorkBuddy；也可通过 `DWS_EXECUTABLE` 或脚本 `--dws` 参数指定实际可执行文件，例如 PowerShell 中的 `--dws "$env:USERPROFILE\.fiboo\bin\dws.exe"`。认证信息由 DWS 管理，不随本插件传递。

## 与其他插件配合

| 插件 | 用途 | 是否必要 |
|---|---|---|
| `dingtalk-cli` | 员工身份认证、资料表读取与文件下载 | 必需 |
| `xiaohongshu-content-planner` | 将资料包接入小红书策划与生产 | 需要内容创作时安装 |
| `wave-image` | 按已确认策划生成或编辑图片 | 本插件查询资料不需要 |
| `xiaohongshu-note-fetch` / `kimi-webbridge` | 获取小红书参考笔记、浏览器资料 | 本插件查询资料不需要 |

资料写入员工选择的任务目录，安装缓存只保存代码与流程。产品资料库变更会在下次实时查询中读取；脚本和流程的变更通过插件市场版本发布。不要把任务资料、产品素材、登录状态或下载结果提交回插件仓库。

## 验证

在插件根目录执行 `python -B -m unittest discover -s skills/fiboo-product-materials/tests -p "test_*.py"`。测试使用模拟 DWS 与合成文件，不登录钉钉、不下载公司资料。
