<p align="center"><img src="assets/brand-logo.png" width="96" alt="写道标志"></p>

# 写道 · Xiedao

免费的开源 AI 小说写作桌面工具。把书架、规划、正文、故事记忆和审稿放在同一个本地工作区，让作者可以查看过程、修改结果、停止任务并继续写作。

**软件免费，源码以 MIT 许可证开放。模型服务由你自行配置；模型服务商可能收取 API 费用。**

[下载 Windows 版](https://github.com/Daizhdd/xiedao/releases/latest) · [使用说明](docs/GETTING_STARTED.md) · [反馈问题](https://github.com/Daizhdd/xiedao/issues) · [参与开发](CONTRIBUTING.md)

## 可以做什么

- 用书架管理作品，在写作书房查看大纲、章节和正文。
- 从概念到全书规划、卷纲、章纲，逐章生成草稿。
- 查看任务进度，停止任务，检查记录并恢复执行。
- 比较重写候选，再决定是否采纳，保留正文版本。
- 结合原文证据审稿，管理设定、故事记忆、伏笔和文风。
- 接入兼容 OpenAI Chat Completions 的服务、DeepSeek 或本地 Ollama。

AI 生成的正文和审稿意见需要作者复核。项目正在持续完善，欢迎提交可以复现的问题。

## Windows 直接使用

1. 在 [Releases](https://github.com/Daizhdd/xiedao/releases/latest) 下载 `xiedao-windows-x64.zip`。
2. 解压到你有写入权限的独立文件夹，运行其中的 `写道.exe`。不需要安装 Python。
3. 在软件的模型配置中填写 API 地址、模型名及你自己的 API Key，设为默认模型。
4. 新建作品，先用少量章节熟悉流程，再逐步扩大写作范围。

下载包不包含书稿、数据库、API Key 或预设付费账户。首次运行会在程序旁创建 `data/`。更新时先关闭写道、备份整个 `data/`，再替换程序；请保留原来的 `data/`。

## 从源码运行

当前验证环境：Windows x64、Python 3.13、PySide6 6.11.2。

```powershell
git clone https://github.com/Daizhdd/xiedao.git
cd xiedao
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

源码运行的数据位于项目目录 `data/novel.db`。其他系统暂未做完整验证。

## 离线验证与打包

测试使用临时数据库和模拟模型，不需要 API Key。

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe test_smoke.py
```

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm xiedao.spec
```

程序生成在 `dist/写道.exe`。打包说明和依赖许可见 [BUILD.md](docs/BUILD.md) 与 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 数据与隐私

作品和模型配置保存在本机 SQLite 数据库中。使用远程模型时，参与请求的正文、设定和上下文会发送到你选择的服务商。API Key 当前保存在本机数据库中，尚未使用系统凭据保险库；请妥善保管 `data/` 和备份。

提交反馈时使用虚构作品并移除密钥，不要上传你的数据库或真实书稿。详见 [SECURITY.md](SECURITY.md)。

## 许可证

写道自有源码及随库品牌资源使用 [MIT](LICENSE) 许可证。PySide6、Qt、Python 等依赖遵循各自许可，详见 [第三方声明](THIRD_PARTY_NOTICES.md)。
