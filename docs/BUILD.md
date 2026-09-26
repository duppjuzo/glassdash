# 开发与打包

## 环境

Windows、Python 3.13、与目标架构匹配的 Python。PyInstaller 不会把 ARM64 解释器变成 x64 程序；两个版本必须分别使用 ARM64 / x64 Python 构建。构建脚本读取 Python PE 机器类型判定架构，避免 Windows ARM 模拟环境的 platform.machine() 歧义。

```powershell
python -m pip install -r requirements-build.txt
python glassdash.py --show
python tests/battery_check.py
python tests/integration_check.py
python glassdash.py --self-test build/source-check.json
python packaging/build.py
```

本次构建环境的准确依赖版本见 `requirements-lock.txt`；需要复现时可用 `python -m pip install -r requirements-lock.txt`。每份二进制包还会记录该架构实际使用的依赖版本。

示例双架构构建：

```powershell
& 'C:\Path\Python313-arm64\python.exe' packaging/build.py
& 'C:\Path\Python313-x64\python.exe' packaging/build.py
python packaging/source_archive.py
```

构建脚本使用固定 spec，以无控制台的目录模式打包。每个目录包含一个 exe 和 `_internal` 动态依赖；无需安装 Python，须保留整个目录。未启用 UPX、未签名。

## 产物与验证

- `dist/GlassDash-arm64/`、`dist/GlassDash-x64/`：解压即用程序目录。
- `release/GlassDash-1.0.0-windows-{arch}.zip`：可分发 ZIP。
- `release/GlassDash-1.0.0-source.zip`：显式白名单源码包。
- `*.sha256`：对应 ZIP 的 SHA-256。
- `build/{arch}/build.log`：构建日志。
- `build/{arch}/self-test.json`：打包后的 exe 自检结果。

每次构建会检查 exe 架构并执行打包程序的 `--self-test`，覆盖 Qt 插件载入、两页 / 充电 / 翻页绘制、WMI 连接、配置存储和 HTTPS 证书。只有这些检查通过才生成发行 ZIP。WMI 无电池不代表程序失败，报告会记录连接和行数。

离屏回归检查使用合成背景，避免对桌面反复抓屏。实际账号额度、长时间稳定性和不同设备硬件需要另外验证。x64 在 ARM Windows 上通过运行不等于已在 Intel / AMD 实机验证。

## 模块边界

- `liquid_glass.py` 保持稳定采样实现；不要恢复旧的高频抓屏或 layered-window 捕获循环。
- `quota_fetchers.py` 保留原 QuotaRing 解析语义；本地与云端 credits 字段的含义不同。
- `battery_stats.py` 只接受实际电池功率，SoC 估算不进入续航与均耗。
- `app_paths.py` 管理用户数据；不能把状态写进 PyInstaller 临时目录或内置资源目录。
- 原先 `probe/` 中的研究脚本和历史截图留在本地，日常回归使用 `tests/`。

## 发布内容

源码包仅包含显式列出的 Python 模块、文档、构建脚本与测试。不打包 `auth.json`、`tokens.json`、个人配置、电池历史、截图、备份或机器探测输出。第三方原始许可证从实际安装依赖中复制到二进制发行包 `licenses/`，安装版本保存在 `build-info.json`。
