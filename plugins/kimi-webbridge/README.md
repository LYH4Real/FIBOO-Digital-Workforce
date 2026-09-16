# Kimi 浏览器助手

公司分发版本 2.0.5，来自现有 Kimi WebBridge Skill 2.0.5。桥接程序由安装器从 Kimi 官方固定版本地址下载并校验 SHA-256，不包含维护者的浏览器登录态或身份文件。

安装插件后告诉 WorkBuddy：“初始化 Kimi 浏览器助手，帮我检查连接”。

1. 从 [Kimi 官方页面](https://www.kimi.com/products/kimi-webbridge)进入 Chrome / Edge 扩展商店，点击添加。
2. 执行插件内 `scripts/setup.ps1`，下载并启动本地桥接程序。
3. 执行 `scripts/setup.ps1 -CheckOnly`，确认 `connected: true`。

浏览器扩展与 WorkBuddy 插件是两个安装位置。WorkBuddy 市场更新技能与安装器；扩展商店管理浏览器扩展版本。Kimi `status` 会报告桥接程序的新版本及扩展版本不匹配，升级前保留当前任务，按 Kimi 官方 `upgrade` 流程对齐。不要在后台任意重启正在使用的浏览器桥接服务。

本包保留现有上游 Skill；原始操作说明位于 `skills/kimi-webbridge`。FIBOO 新增首次安装与诊断脚本，不声明对上游内容的所有权。
