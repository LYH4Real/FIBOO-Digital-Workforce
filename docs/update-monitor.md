# Windows 自动更新检查

WorkBuddy 的原生市场同步仍负责下载和更新已安装插件。此监测器是只读的后备检测：每天本地时间 10:00 和当前员工登录 Windows 时检查版本，不启动原生管理服务，不写 WorkBuddy 的插件注册表、不安装或升级插件。

## 安装

先完成公司市场注册，再运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-update-monitor.ps1 -Source "https://github.com/LYH4Real/FIBOO-Digital-Workforce" -RunNow
```

安装脚本创建 `\FIBOO\FIBOO-Marketplace-UpdateCheck` 计划任务。任务以当前登录员工的普通权限运行，不保存 Windows 密码；PowerShell 使用 `-WindowStyle Hidden`。未登录时不会要求输入凭据，下次登录和 `StartWhenAvailable` 会补充执行。部分公司设备限制 Task Scheduler/CIM；如果安装返回“拒绝访问”，须由设备管理员允许该任务，不能把脚本返回错误当作已安装成功。

脚本将监测器及只读管理器复制到员工的 `%USERPROFILE%\.fiboo\marketplace\scripts`，因此原下载文件夹移走后任务仍可运行。再次运行安装脚本可刷新监测器、配置和同名任务。它只覆盖具有相同 FIBOO 标记和状态目录的任务；不会覆盖其他任务。

安装器本身也保存在上述 `scripts` 目录；原下载文件夹移走后，仍可从该位置使用 `install-update-monitor.ps1 -CheckOnly` 或 `-Uninstall`。

Python 优先使用 WorkBuddy 的默认环境，其次使用 WorkBuddy 提供的版本目录，再使用系统 Python；均需 3.10+。可以用 `-PythonPath` 显式指定，不要求员工另装系统 Python。Git 使用员工现有的 WorkBuddy 随包 Git 或 PATH Git；不会索取、复制或写入 Git 凭据。

## 员工能看到什么

- 状态：`%USERPROFILE%\.fiboo\marketplace\update-status.json`。
- 可直接打开的报告：`%USERPROFILE%\.fiboo\marketplace\update-report.html`。
- 默认尝试 Windows 本地气泡通知，仅当已安装插件的可用版本发生差异，且与上次成功通知的差异集合不同。只显示插件名与版本，不包含业务资料，不发送给其他人。

通知进程隐藏运行，气泡驻留 6 秒，进程限时 10 秒。系统不支持 WinForms 或调用失败时写 `notificationSupported: false`，检测仍成功并保留 HTML 报告。`notificationRequested: true` 表示系统通知调用已成功发出，不保证 Windows 的专注模式/通知设置允许实际显示。安装时加 `-NoNotify` 可以关闭气泡；检测及报告继续运行。

`checkedAt` 是本次检查时间；`comparisons` 是逐插件的已安装/可用版本及差异。`success: false`、`stale: true` 表示本次失败，此时保留 `lastSuccessfulCheck` 和上次比较结果，并写 `errors`，不能把旧结果看成本次刚查到的状态。离线或临时错误不会反复弹出通知。

报告路径也适合由公司插件的 SessionStart 指引引用；监测器本身不会注入或改写其他插件的 hook。通知提示有变化后，员工可在 WorkBuddy 技能/插件页查看，或按公司文档运行原生市场同步。它不会与原生自动更新发生双重写入。

## 避免重复下载

远程 Git 来源先执行有 30 秒超时的 `git ls-remote HEAD`。HEAD 与上次成功记录相同时，复用已知的远端版本，并重新读取本机已安装版本进行比较。发现新安装而缓存未知的插件、HEAD 变化或使用 `-NoHeadCache` 时，通过 partial clone 和 sparse checkout 仅读取市场与插件 JSON 清单。公司 GitHub 实测没有下载 EXE/DLL，工作树仅 4,045 字节；不会为了比较版本反复下载全部程序。

本地市场没有远端 HEAD，每次直接读取。克隆与版本检查沿用管理器的 120 秒边界；任务整体限时 5 分钟。Git 禁止交互式凭据提示，SSH 默认采用 BatchMode。检查失败时只更新监测器自己的状态，不修改市场来源或已安装插件。

远端 HEAD 检查与目录拉取共用管理器的 Git 网络环境：未显式配置时临时继承 Windows 系统代理；已有代理/no_proxy 环境变量或 Git 代理配置优先，包括用空值明确禁用代理的设置。代理只传给本次子进程，不打印代理值，也不改写系统环境或 Git 配置。

## 手动检查、隔离测试与卸载

```powershell
# 立即只读检查；不注册任务
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\update-monitor.ps1 -NoNotify

# 检查专属任务是否存在
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-update-monitor.ps1 -CheckOnly

# 停止并删除该专属任务；保留报告和监测器文件，也不改变已安装插件
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-update-monitor.ps1 -Uninstall
```

参数 `-TaskName FIBOO-Test-...`、`-StateDirectory <独立目录>`、`-ConfigDirectory <独立WorkBuddy配置目录>` 用于隔离验证。任务名必须以 `FIBOO-` 开头，TaskPath 固定为 `\FIBOO\`；卸载隔离任务时必须提供相同 TaskName 和 StateDirectory。

回归测试默认不创建计划任务：

```powershell
python -B -m unittest discover -s tests -p "test_update_monitor.py" -v
```

明确进行隔离 Task Scheduler 实测时，将进程环境变量 `FIBOO_TEST_SCHEDULED_TASKS` 设为 `1` 后运行相同命令。测试创建随机 FIBOO 测试任务，使用临时配置与本地合成版本，禁用气泡，验证启动后写出报告，并在 finally 中删除任务；不注册真实员工的长期监测任务。

2026-09-16 已完成 Windows Scheduler 隔离验收：8 项测试全部通过，包括真实任务注册、启动、生成版本差异报告和卸载，测试总耗时 5.784 秒。首次在受限执行环境中调用 CIM 被拒绝，随后在获授权的系统执行环境通过；员工设备仍需允许当前用户访问 Task Scheduler/CIM。这项验收证明计划任务自动检测链路，不证明已安装插件完成了 WorkBuddy 原生自动升级，也不等于真实员工的长期任务已经安装。
