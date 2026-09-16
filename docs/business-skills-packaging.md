# 业务 Skill 插件打包记录

核对日期：2026-09-16。目标为 Windows WorkBuddy 的 `.codebuddy-plugin` 插件格式。

## 源版本与分发版本

| 插件 | 权威来源 | 源版本 | 市场分发版本 |
|---|---|---|---|
| `fiboo-product-materials` | 原 `fiboo-product-materials-skill/fiboo-product-materials` 源项目；与现有 Codex、WorkBuddy 安装中的 SKILL、requirements 和四个脚本哈希一致 | 0.1.0 | 0.1.0 |
| `xiaohongshu-content-planner` | 原 `小红书全域运营/xiaohongshu-content-planner` 源项目；版本高于已安装的 0.3.0 | 0.3.1 | 0.3.2 |

源目录和已有安装均未修改。市场内 planner 0.3.2 是明确的新分发适配版本；保留原流程，并在 image_request_guard.py 增加当前 Wave 0.4.2 的 auto 响应格式兼容。FIBOO 的四个 Python 脚本及 PDF 依赖文件未改变；技能入口补充可移植运行说明。

## 包含范围

- 两个插件清单、README、插件 CHANGELOG。
- Skill 入口及运行必需的 `scripts/`、`references/`、PDF requirements；planner 保留 Skill 版本及历史变更记录。
- 原项目的合成离线回归测试，放在各 Skill 的 `tests/`，用于维护回归。

没有复制 `_user_meta.json`、`agents/openai.yaml`、本地绝对安装目录、认证配置、产品图片/PDF、真实业务资料包、历史策划/产物、Python 缓存或源项目的 Git 目录。FIBOO 公司钉钉资料库的 Base/Table/View 标识和入口保留为必要的内部服务定位；访问仍依赖员工自己的权限，不含令牌或授权。

测试中的 `C:/runs`、`file:///D:/file.pdf`、`token=never-print`、`secret` 等为合成非法输入/泄露防护案例，不是员工路径、真实凭据或可执行配置。

## 依赖与流程

`fiboo-product-materials` 的原生 `dependencies`：`dingtalk-cli`。

`xiaohongshu-content-planner` 的原生 `dependencies`：`kimi-webbridge`、`dingtalk-cli`、`wave-image`、`fiboo-product-materials`、`xiaohongshu-note-fetch`。

原生依赖安装完成后，DWS 本人登录、浏览器扩展连接和生图服务配置仍按各插件初始化流程执行。Python 3.10+ 为业务脚本要求；工作站引导可检查系统或 WorkBuddy 提供的可用运行时。PDF 文本提取另需 `pypdf`。

planner 新增 `references/fiboo-marketplace-workflow.md`：从实时资料和参考笔记开始，保留产品记录身份、规格、来源页码、原图顺序、真实文件路径，映射到原来的事实/参考/策划文件合同。原文档的 `product-knowledge-contract.md` 已是通用文件接入，并未硬依赖旧知识库服务；此次增加 FIBOO 的具体接入约定。

本地产品缓存的哈希检查只证明本地未变。新篇目读取 FIBOO 时需核对实时记录；依赖云端 PDF 的任务需重新下载核对其内容哈希。实时读取失败时不能宣称“已更新到最新资料”。

## 已执行检查

使用 Python 3.14.7 在打包后的插件目录执行：

```powershell
python -B -m unittest discover -s skills/fiboo-product-materials/tests -p "test_*.py"
python -B -m unittest discover -s skills/xiaohongshu-content-planner/tests -p "test_*.py"
```

上面两条命令分别在对应插件根目录运行。结果：FIBOO 38 项通过；planner 增加真实 Wave 工具合同测试后 142 项通过。测试没有调用真实钉钉、付费生图或发布操作。

另外已核对：两个清单的名称/版本/技能入口、未调整的生产脚本与源文件字节一致、planner 的 55 条本地 Markdown 文件链接均存在、包内无员工缓存/用户配置。路径示例使用当前任务约定，实际执行前必须替换真实绝对路径；未硬编码宿主 MCP 工具前缀。

系统 `skill-creator/scripts/quick_validate.py` 已尝试运行，但当前系统和 Codex bundled Python 都缺少 PyYAML，因此该特定 helper 未运行成功。Skill frontmatter 保持上游结构，仅 planner 的描述文字作品牌替换。该 helper 的缺失不等于 WorkBuddy 已做加载验收；市场整体的原生解析、安装、启用与更新验证由市场级验证负责，不能用离线测试代替。

## 后续维护

先修改市场仓库内对应插件，再运行离线测试与市场级检查；变更必须发布新的插件版本并补 CHANGELOG。不要将员工运行时缓存或业务材料回灌为 Skill 内容。若再从原项目同步，先比较版本及差异，保留 0.3.2 添加的 FIBOO 接入和依赖，避免直接覆盖导致工作流回退。
