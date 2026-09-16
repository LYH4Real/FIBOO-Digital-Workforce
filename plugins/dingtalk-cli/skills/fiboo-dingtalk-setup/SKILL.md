---
name: fiboo-dingtalk-setup
description: 初始化 FIBOO 钉钉办公插件、安装 DWS 命令、检查钉钉 CLI、引导员工独立登录时使用。
---

# 初始化钉钉办公

Windows x64 的 CLI 已包含在 `${CODEBUDDY_PLUGIN_ROOT}/bin/dws.exe`，可以直接调用。

首次为员工准备其他插件所需的稳定命令路径时，执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "${CODEBUDDY_PLUGIN_ROOT}/scripts/setup.ps1"
```

这会复制程序到员工 `~/.fiboo/bin/dws.exe` 并加入用户 PATH。当前 WorkBuddy 若仍找不到 `dws`，使用该绝对路径；fiboo 产品资料 helper 可用 `--dws` 指定它。不覆盖其他目录里的 DWS，也不复制任何维护者登录状态。

登录前先读取本插件 CLI 的 `auth --help`，按实际命令让员工完成自己的浏览器或扫码授权；不要索取账号口令、Token，不将授权信息写入插件包。授权后按业务所需最小范围验证，不发送测试消息。

后续钉钉操作按对应 dingtalk-* 技能执行。升级后再次执行 setup 可刷新稳定路径，已存在的登录信息保持独立。
