# 构建 Windows 程序

Mac 的原生构建、自动构建和验证步骤见 [MACOS.md](MACOS.md)。Windows 与 Mac 共享应用源码，分别使用 `xiedao.spec` 和 `xiedao-macos.spec`。

## 环境

首个开源发布验证使用 Windows x64、CPython 3.13.15、PySide6 / Shiboken6 6.11.2 和 PyInstaller 6.22.3。

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe test_smoke.py
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm xiedao.spec
```

`xiedao.spec` 只打包入口、依赖、图标和品牌图片，不打包 `data/`。它保留单文件程序结构，并去掉部分打包环境发现的重复根目录 Qt DLL。

首次开源发行使用已验证的 2026-10-03 程序；构建工具、依赖与环境可能影响字节级重现，不能仅凭重新打包后的哈希不同判定源码不同。

## 发布检查

- 在独立、可写的空文件夹启动候选程序。
- 首次生成的数据库应为空，无作品、无模型配置。
- 下载包只含程序、使用说明、许可文本与第三方声明。
- 核对程序归档不含数据库、私人文件或旧备份。
- 生成 ZIP 和程序的 SHA-256，并随 Release 发布校验文件。
- 保留依赖版权声明及许可证。Qt 插件可能采用不同于核心库的许可证，详见第三方声明；发布衍生二进制时须再次核对实际依赖。

## 替换或调试 Qt 依赖

写道源码和 spec 完整公开，可修改构建配置或换用自行构建的 PySide6 / Qt 后重新打包。Qt DLL 在 PyInstaller 运行时临时展开目录中动态加载，未静态链接进写道自有代码。

依赖源码位置、版本、许可和重新构建参考见 `THIRD_PARTY_NOTICES.md`。写道不禁止对依赖进行修改、替换或为调试这些修改而进行逆向工程。
