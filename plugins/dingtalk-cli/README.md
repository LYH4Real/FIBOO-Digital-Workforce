# 钉钉办公

市场版本：1.0.61。内置 DWS CLI 1.0.61（Windows x64）和现有的 14 个钉钉产品技能。

安装插件后，告诉 WorkBuddy：“初始化钉钉办公插件，检查 DWS，然后引导我登录”。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<插件目录>\scripts\setup.ps1"
dws auth --help
```

`setup.ps1` 将程序安装到员工自己的 `~/.fiboo/bin/dws.exe`，并把该目录加入用户 PATH。重启 WorkBuddy 后新 PATH 生效；当前任务可以直接调用这个绝对路径。重复执行会比较 SHA-256；更新程序不改动 DWS 的账号文件。

各技能中的 `dws` 是本插件 `bin/dws.exe` 的简称；WorkBuddy 可以直接使用插件内的可执行程序，无需依赖系统原有版本。不要把维护者的登录状态分发给员工。

插件升级会携带新的固定版本 DWS；再次执行 setup 才更新稳定的辅助路径。技能直接使用插件内版本时随插件更新生效。钉钉资料访问仍取决于员工自己的组织权限。

上游：https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli 。保留上游 Apache-2.0 许可证。
