# 第三方组件

本发行包包含 Python、Qt / PySide6 / Shiboken、NumPy、Pillow、comtypes、Requests 及其依赖，以及 PyInstaller 引导程序。具体安装版本见 `build-info.json`，随安装包提供的许可证和版权说明汇总在 `licenses/`。项目源码包不包含用户账号或令牌。

上游项目与源码入口：

- Python：https://www.python.org/ / https://github.com/python/cpython
- Qt：https://code.qt.io/qt/qtbase.git/
- PySide / Shiboken：https://code.qt.io/pyside/pyside-setup.git/
- NumPy：https://github.com/numpy/numpy
- Pillow：https://github.com/python-pillow/Pillow
- comtypes：https://github.com/enthought/comtypes
- Requests：https://github.com/psf/requests
- urllib3：https://github.com/urllib3/urllib3
- certifi：https://github.com/certifi/python-certifi
- charset-normalizer：https://github.com/jawah/charset_normalizer
- idna：https://github.com/kjd/idna
- PyInstaller：https://github.com/pyinstaller/pyinstaller

Qt / PySide 以独立动态库存放于 `_internal`，不修改上游库。重新构建所需项目源码与打包脚本随独立源码包提供。各依赖的使用和再分发遵循其各自许可证；本说明不为 GlassDash 项目另行指定开源许可证。

另附 Qt 上游 LICENSES 目录中的 LGPL-3.0-only、GPL-3.0-only、GPL-2.0-only 和 Qt-GPL-exception-1.0 原文，位于 `licenses/Qt/`；来源为 https://github.com/qt/qtbase/tree/dev/LICENSES 。允许按相应许可证替换这些动态库；本程序不额外限制为调试库修改而进行的逆向工程。

额度读取模块沿用本地 QuotaRing 项目；其中 AGY 云端逻辑的原注释注明移植自 antigravity-usage（MIT），相关出处注释保留在 `quota_fetchers.py`。原始 MIT 许可证及版权说明已收录到 `licenses/antigravity-usage/LICENSE.txt`（源码目录为 `packaging/licenses/antigravity-usage/`），上游：https://github.com/skainguyen1412/antigravity-usage 。
