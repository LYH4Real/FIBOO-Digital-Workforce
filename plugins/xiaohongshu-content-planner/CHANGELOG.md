# 变更记录

## 0.3.2 — 2026-09-16

- 将源技能 0.3.1 打包为 FIBOO 市场中的 WorkBuddy 插件。
- 新增 `fiboo-product-materials` 实时资料交接；保留 recordId、规格、来源页码与本地文件身份，不把来源卖点自动升级为已批准宣传。
- 明确钉钉 CLI、小红书笔记获取、Kimi 浏览器连接和 Wave Image 的工作分工与运行前检查。
- 将文档路径示例改为当前任务路径约定，去掉个人标识及旧任务表述。
- 保留源 0.3.1 的用户确认与人工审稿要求；将图片请求预检适配 Wave 0.4.2 的 `response_format:auto`，并以随包 MCP 的真实工具 schema 做离线契约测试。
- 更新生图合同：按真实内容选择图片扩展名，文件重名时增加序号；批量 manifest 仍需使用独立运行目录。

原技能完整变更记录见 `skills/xiaohongshu-content-planner/CHANGELOG.md`。
