# 写道 Mac 测试版

## 选择下载包

- Apple 芯片（M 系列）：`xiedao-macos-arm64.zip`。
- Intel 芯片：`xiedao-macos-x86_64.zip`。

在苹果菜单的“关于本机”查看芯片类型。测试包要求 macOS 13 或更新系统；自动构建和应用启动验证运行在 macOS 15 上，其他系统版本尚未验收。

解压，将 `写道.app` 拖入“应用程序”后打开。无需安装 Python。模型服务仍由用户配置，软件不附赠 API 额度。

本批为预发布测试包，只采用构建工具的本地签名（ad-hoc），未使用 Apple Developer ID 签名，也未完成苹果公证。macOS 可能提示无法验证开发者并阻止启动。确认下载来自 `Daizhdd/xiedao` 并核对 SHA-256 后，可在“系统设置 → 隐私与安全性”查看系统提供的“仍要打开”入口。无需关闭整个系统的安全保护。

ZIP 旁提供对应架构的 SHA-256 校验文件；终端进入下载目录后执行：

```bash
shasum -a 256 -c SHA256SUMS-macos-arm64.txt
# Intel 使用 SHA256SUMS-macos-x86_64.txt
```

## 数据、更新和 Windows 迁移

Mac 上，作品、模型配置、API 日志和自动备份统一保存在：

```text
~/Library/Application Support/写道/
```

在 Finder 的“前往 → 前往文件夹”粘贴这个路径。数据库名为 `novel.db`。更新前退出写道、备份整个目录，再替换“应用程序”中的 `写道.app`；保留数据目录。

从 Windows 迁移：

1. 退出两边的写道，备份 Windows 程序旁整个 `data/`。
2. 在 Mac 首次打开写道，随后退出。
3. 将 Mac 当前数据目录复制到另一个安全位置作为回退备份。
4. 将 Windows 的 `data/` 中全部内容复制到 Mac 的上述数据目录。不要只复制数据库而漏掉未完成任务的恢复标记和备份文件；两端存在作品时，先选择要迁移的完整数据集，不要直接覆盖另一端作品。
5. 重开 Mac 写道，核对书架、正文、版本和模型配置。

数据库中含 API Key，请不要公开上传。原有 Ollama 或其他本地模型需要在 Mac 单独配置。

Mac 编辑器使用 `Command+S` 保存、`Command+G` 生成正文。中文界面优先使用系统苹方字体。

## 源码运行与本地构建

需要 macOS 与对应架构的 Python 3.13。分别使用 Apple 芯片和 Intel 的原生环境构建，不依赖 Rosetta 混合环境。

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-build.txt
python app.py
```

离线验证和打包：

```bash
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -q
python test_smoke.py
python scripts/build_macos.py --arch arm64
# Intel 使用 --arch x86_64
```

脚本从现有品牌图片转换 `.icns`，构建包含 Qt 动态库的 `写道.app`，校验本地签名，启动打包后的 Cocoa 应用，保存书房与编辑器截图，验证空数据库、正文保存、版本、备份恢复和数据库重开，再使用 `ditto` 生成保留符号链接与执行权限的 ZIP。输出位于 `release-macos/`，截图与报告位于 `build/verification-<架构>/`。

`.github/workflows/macos.yml` 在 `macos-15` 和 `macos-15-intel` 执行上述步骤，成功后保存两个架构的下载产物。仅上传通过验证的包到 Release；构建失败时保留验证报告供诊断。打包采用源码和资源白名单，不包含用户小说、数据库或账户配置。

## 本批验证范围

自动化包含离线模型模拟回归和 GitHub Mac 环境中的打包应用启动、界面渲染、编辑保存、版本和备份恢复。没有调用付费模型。云端截图不能替代普通 Mac 用户的人工验收；输入法候选窗、Finder 下载后的 Gatekeeper 行为、长时间写作和真实服务商调用仍需真机反馈。

正式广泛发行前，应配置 Apple Developer ID 签名和苹果公证。用户自己的证书、私钥和公证凭据只能保存在本地钥匙串或 GitHub Secrets，不能提交到公开仓库。
