# GlassDash

Windows 液态玻璃桌面仪表盘：功耗、充电、续航与 AGY / Codex 额度，两页上下立体翻转。

## 下载与运行

**[下载最新版本](https://github.com/duppjuzo/glassdash/releases/latest)** · [ARM64 包](https://github.com/duppjuzo/glassdash/releases/download/v1.0.1/GlassDash-1.0.1-windows-arm64.zip) · [x64 包](https://github.com/duppjuzo/glassdash/releases/download/v1.0.1/GlassDash-1.0.1-windows-x64.zip)

| 版本 | 适用电脑 | 启动文件 |
|---|---|---|
| Windows ARM64 | 骁龙等 Windows on ARM 设备 | `GlassDash-arm64.exe` |
| Windows x64 | Intel / AMD 64 位 Windows 设备 | `GlassDash-x64.exe` |

发布文件位于 `release/`。**完整解压 ZIP 后运行 exe**，保留旁边的 `_internal` 文件夹。不需要安装 Python，不需要管理员权限。首次启动显示仪表盘，关闭按钮会隐藏到托盘；右键托盘选择“退出”才会结束程序。两个版本使用同一份用户设置，同一时间只运行一个实例。

完整操作说明见 [使用说明](使用说明.md)，源码构建见 [开发与打包](docs/BUILD.md)。

## 功能

- 250 × 150 液态玻璃悬浮窗，透明高光、边缘折射、轻微模糊，支持拖动。
- 功耗页：实际电池放电功率、充电净输入功率、预计剩余续航、距检测到充满时间、累计电池使用时长、平均功耗与亮度。
- 额度页：AGY / Codex 双环、剩余额度；后台刷新，不弹出鼠标悬停详情。
- 滚轮、↑↓ / PageUp / PageDown、右侧箭头进行上下立体翻页。
- 托盘切换页面、刷新额度、调整刷新间隔、Google 登录、开机自启。

## 系统与数据限制

面向 Windows 11 桌面环境；设备需提供 WMI 电池信息才能显示实际功率和续航，台式机或不支持该接口的驱动可能显示“—”。x64 发行包在本机 ARM64 Windows 的兼容环境中验证，尚未在独立 Intel / AMD 电脑上进行硬件实测。

| 数值 | 计算或含义 |
|---|---|
| 整机功耗 | `BatteryStatus.DischargeRate`，电池放电时的实际读数 |
| 充电功率 | `BatteryStatus.ChargeRate`，流入电池的净功率，不等于充电器插座功率 |
| SoC 估算 | 仅 X1E-78-100 使用已有的 4P+8E 模型；其他处理器不套用此模型 |
| 预计续航 | 剩余电量 ÷ 约 60 秒时间常数平滑后的放电功率，随负载变化 |
| 已用 / 均耗 | 已记录的连续有效电池运行时长 / 该时段功率积分平均值 |
| 距充满 | 距程序检测到接电、停止充电且达到满充容量 99% 的时间 |

未记录的历史不回填；休眠、关机、程序退出及超过 10 秒的采样空档不计入使用时长。充满后进入新统计周期；记录不完整时界面有提示。保养模式将充电上限设为 80% 时，不会将其当作 100% 满充。

玻璃采样保持已修复的稳定性限制：一次探测、不可用时停用；正常采样上限约 8 Hz，动画约 20–30 fps。安全回退时保留透明与高光。部分截图工具可能无法录到窗口，这是捕获排除机制的表现。

## 设置与隐私

- 设置与统计：`%LOCALAPPDATA%\GlassDash`。更新版本、移动解压目录不会丢失这些记录。
- 启动时可迁移程序旁的旧 `battery_stats.json` / `quota_config.json`，原文件保留。
- Codex 复用当前用户的 `~/.codex/auth.json`；AGY 本地模式复用已登录的应用。Google 云端登录需先在额度设置中填写你有权使用的 OAuth 客户端 ID 和密钥，发行包不内置客户端密钥。
- 软件包不包含个人账号、令牌、历史电池统计或截图；正常桌面采样只在内存中处理。
- 依赖版本与第三方说明随发行包提供，见 `build-info.json`、`licenses/` 和 [第三方说明](THIRD_PARTY_NOTICES.md)。

## 源码结构

```text
glassdash.py          入口、WMI / PDH、电池数据、窗口、托盘
glass_pages.py        双页投影和翻页交互
liquid_glass.py       稳定版桌面采样与玻璃渲染
quota_dashboard.py   额度后台刷新、设置、双环绘制
quota_fetchers.py    沿用 QuotaRing 的账号与额度读取
battery_stats.py     电池周期、续航和持久统计
app_paths.py         版本、用户数据目录、自启命令
oauth_config.py      本机 Google OAuth 客户端配置
diagnostics.py       打包程序自检
glass_preview.py     合成预览背景
tests/               无桌面捕获的回归检查
packaging/           构建、封装与源码归档脚本
docs/                构建说明
```

本地 `backup/`、`probe/` 保留历史备份和研究资料，不进入发布源码包。`build/`、`dist/` 是构建产物，`release/` 是交付文件。
