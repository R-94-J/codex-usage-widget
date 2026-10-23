# Codex 用量条

一个贴在 Windows Codex / ChatGPT 桌面主窗口上的常驻统计条。**不修改 Codex，不读取 API Key，不上传会话，不调用模型产生额外 token 费用。**

## 启动

双击 `dist/CodexUsageWidget.exe`。本机也提供 `Launch.vbs`（无黑色控制台闪烁）和 `Start.cmd`。
程序自带运行时，使用打包版不需要安装 Python，不需要管理员权限。

- 在 Codex / ChatGPT 桌面窗口处于前台时显示；切到别的软件、最小化宿主时隐藏，回到宿主时恢复。
- 没有打开宿主时在后台等待，Windows 托盘可找到柱形图图标。
- 只允许一个实例；再次打开不会叠加重复状态条。
- 没有添加开机启动。退出状态条不会退出 Codex。

## 操作

| 操作 | 效果 |
| --- | --- |
| 拖动没有按钮的区域 | 调整与宿主窗口的相对位置，自动保存 |
| 向下/向上箭头 | 展开或收起输入、缓存、输出明细 |
| 齿轮 | 设置单价、币种、渠道倍率，锁定统计会话 |
| × | 退出用量条 |
| 右键 | 明细、设置、恢复位置、退出 |
| 托盘图标双击 / 右键 | 打开设置 / 菜单 |

默认展示 **最近活跃主会话**，不是保证识别当前鼠标选中的聊天。需要稳定查看某个聊天时，在设置中锁定对应会话。后台子代理不会抢走自动选择；今日统计包含其独立日志中的用量。

## 费用的含义

**所有金额都是估算，不是实际扣费、账户余额或订阅额度。**

- 内置参考价格快照日期：**2026-10-23**，单位为美元/百万 token，详见 `docs/evidence/pricing-sources.md`。
- 第三方渠道可能有不同价格、倍率和缓存计费方式。打开齿轮，选模型，勾选“启用自定义单价”，分别填写非缓存输入、缓存读、缓存写和输出单价。
- 支持 USD（美元）和 CNY（人民币），**切换币种不会自动换算**。请直接填写对应币种的费率。
- `渠道倍率` 默认为 1，费用 = 按四类 token 计算的金额 × 倍率。
- 单次请求的长上下文、已识别的服务档位按参考规则计算；自定义价格可选是否沿用长上下文阶梯。
- 未知模型、缺失请求明细或未知收费档位显示“未定价”。`≥` 表示只算了已定价的部分，不是完整费用。
- ChatGPT 订阅的包含额度不能换算成实际扣费。使用订阅登录时，参考金额只表示按 API 费率的等价估算。
- 不含图片生成、搜索等独立工具费用、税费、平台折扣、返利、最低消费等；不自动联网更新价格。

## Token 口径

只读 `$CODEX_HOME/sessions` 和 `$CODEX_HOME/archived_sessions`，默认是用户目录下的 `.codex`。
按 `token_count` 的累计差分计数，**输入已包含缓存；输出已包含推理；总量只算输入 + 输出。**
累计事件不会重复求和；相同会话文件的归档或复制会去重。启动扫描本机日志，后续增量读取，默认每 2 秒检查一次。数字在 Codex 写入新的用量事件后才变化，**不是逐字流式的实时 token 计数器**。

今日按 Asia/Shanghai（UTC+8）的事件日期划分，包含旧会话今天新增的用量。缺失历史的初始累计仍可计入会话总量，但不假装知道它发生在哪一天。明确标记的分叉历史会被排除。若累计计数重置同时夹有缺失累计字段的调用，无法确认的重叠不额外累加，并提示明细不完整；不能据此还原精确账单。

仅覆盖本机已有日志，不是跨设备、云端或整个账户的总用量；如果平台没有把调用写入日志，就无法统计。默认每次启动重新读取元数据，不维护包含对话正文的数据库。

## 本地文件

设置和错误日志在 `%LOCALAPPDATA%/CodexUsageWidget/`：

- `settings.json`：位置、模型单价、渠道倍率、会话锁定。
- `widget.log` / `last-error.log`：发生异常时的诊断，不记录对话内容。

恢复默认：退出工具后将该目录的 `settings.json` 改名，再启动即可。不要删除 Codex 本身的日志。

## 开发和验证

源码以 Python 3.12、Tkinter 和 ctypes 实现，运行时仅用标准库。PyInstaller 只在打包时使用。

```powershell
cd E:\codex_wj\codex-usage-widget
D:\python3.12\python.exe -m unittest discover -s tests -v
D:\python3.12\python.exe tools\smoke_ui.py
powershell -NoProfile -File tools\build.ps1
D:\python3.12\python.exe tools\smoke_ui.py --exe dist\CodexUsageWidget.exe
```

直接运行打包后的 exe 无需运行构建脚本，也无需更改 PowerShell 策略。

只读统计诊断（不弹界面）：

```powershell
.\dist\CodexUsageWidget.exe --snapshot --output .\usage-snapshot.json
```

UI 冒烟测试创建并操作自己的临时宿主窗口，不移动或最小化真实 Codex。截图、统计核对和测试结果保存在 `docs/evidence`。
