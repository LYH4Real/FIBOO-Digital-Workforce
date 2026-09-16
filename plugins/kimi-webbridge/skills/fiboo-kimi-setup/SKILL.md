---
name: fiboo-kimi-setup
description: 安装或初始化 FIBOO Kimi 浏览器助手、连接 Chrome/Edge 扩展、检查 Kimi 更新或未连接问题时使用。
---

# Kimi 首次配置与检查

先运行只读检查：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "${CODEBUDDY_PLUGIN_ROOT}/scripts/setup.ps1" -CheckOnly
```

员工要求初始化且本地程序不存在时，执行同一脚本去掉 `-CheckOnly`。脚本从 Kimi 官方固定版本下载并核对哈希，已存在的程序不覆盖，不复制任何浏览器身份文件。

浏览器扩展需要员工从 https://www.kimi.com/products/kimi-webbridge 打开的官方商店页面点击安装；可以提供此入口，不能把 WorkBuddy 插件安装成功当成扩展已连接。

`connected=true` 才说明桥接已连接。`updateAvailable` 只表示上游有新程序，`versionMismatch` 表示扩展和程序需要对齐。按照原 `kimi-webbridge` 技能的维护规则处理，正在执行浏览器任务时不自动 stop/restart/upgrade。扩展商店升级由浏览器负责；本市场版本由公司维护者发布。
