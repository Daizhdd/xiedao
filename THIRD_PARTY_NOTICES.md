# Third-party notices

写道自有源码使用 MIT。Windows 单文件程序同时分发以下独立依赖；它们的许可不因写道采用 MIT 而改变。

Mac 测试版采用相同的 PySide6 / Qt 和 PyInstaller 版本，以 `.app` 内动态库形式分发。实际 Python、SQLite 和平台版本记录在每个包对应的 `build-info-macos-<架构>.json`；Mac 不分发 Microsoft Visual C++ 运行库。包内保留本文件和 `licenses/third-party/`，并公开 Mac 构建脚本及 spec，便于替换依赖后重新构建。下表中 Windows 的 OpenSSL / SQLite 版本不代表所有 Mac 构建环境的版本。

| Component | Version | License / source |
| --- | --- | --- |
| CPython | 3.13.15 | PSF license; [source](https://www.python.org/downloads/release/python-31315/) |
| PySide6 / Shiboken6 | 6.11.2 | LGPL-3.0 / GPL-3.0 alternatives; [source](https://github.com/pyside/pyside-setup/tree/v6.11.2) |
| Qt Core, Gui, Widgets, Network | 6.11.2 | LGPL-3.0 / GPL-3.0 alternatives; [source](https://github.com/qt/qtbase/tree/v6.11.2) |
| Qt SVG / image format plugins | 6.11.2 | Applicable Qt and third-party licenses; [SVG source](https://github.com/qt/qtsvg/tree/v6.11.2), [imageformats source](https://github.com/qt/qtimageformats/tree/v6.11.2) |
| Qt Virtual Keyboard plugin | 6.11.2 | GPL-3.0 in the open-source distribution; [source](https://github.com/qt/qtvirtualkeyboard/tree/v6.11.2) |
| PyInstaller bootloader | 6.22.3 | GPL with the bootloader distribution exception; [source](https://github.com/pyinstaller/pyinstaller/tree/v6.22.3) |
| OpenSSL runtime | 3.0.21 | Apache-2.0; [source](https://github.com/openssl/openssl/tree/openssl-3.0.21) |
| SQLite and other CPython dependencies | SQLite 3.50.4 / bundled with CPython | See Python license collection and [SQLite](https://sqlite.org/copyright.html) |
| Microsoft Visual C++ / Universal C runtime | Bundled redistributable files | Microsoft redistributable terms; [reference](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files) |

Copyright in these components belongs to their respective authors, including the Python Software Foundation, The Qt Company Ltd., Qt contributors, PySide contributors, PyInstaller contributors, and OpenSSL contributors. Original copyright notices and license text are preserved under `licenses/third-party/`.

`licenses/third-party/` includes upstream Qt license collections and attribution JSON for the modules above, the PySide6 LGPL text, Python's full license collection, PyInstaller's exception text, and the OpenSSL license. `SOURCES.json` records upstream origins. Some collected notices apply to source features not exercised by this application; collection does not imply every optional feature is included.

The existing 2026-10-03 executable contains a Qt Virtual Keyboard plugin supplied by the packaging environment. That plugin uses GPL-3.0 in the open-source distribution. The binary distribution must retain its applicable GPL notices and source availability; do not describe the complete bundled executable as exclusively MIT. The application's own source remains MIT.

The complete application source and build specification are in this repository. Exact-version Qt / PySide dependency source is available through the links above and [Qt's source archive](https://download.qt.io/archive/qt/6.11/6.11.2/submodules/). Build instructions are in [BUILD.md](docs/BUILD.md); Qt for Python build instructions are in [the upstream documentation](https://doc.qt.io/qtforpython-6/building_from_source/index.html).

You may rebuild the application against modified dependencies. This project does not restrict replacement of Qt/PySide6 libraries or reverse engineering necessary to debug modifications to those libraries. Preserve upstream license requirements when redistributing the original binary or building derivatives.

For authoritative component details, see [Qt for Python licensing](https://doc.qt.io/qtforpython-6/licenses.html) and [PyInstaller licensing](https://pyinstaller.org/en/stable/license.html).
