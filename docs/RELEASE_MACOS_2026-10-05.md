写道首次提供 Mac 测试版。小说规划、编辑、故事记忆、审稿及任务恢复功能共用 Windows 版源码，Mac 使用独立的用户数据目录，更新应用时保留小说。

按“关于本机”中的芯片类型下载：

- **Apple 芯片（M 系列）**：`xiedao-macos-arm64.zip`
- **Intel**：`xiedao-macos-x86_64.zip`

解压后把 `写道.app` 拖入“应用程序”，无需安装 Python。要求 macOS 13 或更新系统；本批自动构建与应用运行检查使用 macOS 15。

作品、模型配置、日志及备份位于 `~/Library/Application Support/写道/`。更新前退出写道并备份整个目录，再替换 `.app`。使用 `Command+S` 保存、`Command+G` 生成正文，中文界面优先使用系统苹方字体。

两个架构均经过 231 项离线回归、完整界面冒烟及打包后的原生 Cocoa 应用检查：首次空数据库、图标与界面渲染、编辑保存、版本快照、备份恢复和数据库重开。包中携带公共根证书，保留 HTTPS 的主机名和证书校验，并通过打包应用的 GitHub 公共网页 HTTPS 请求验证。测试使用虚构作品，未调用付费模型，未打包小说、数据库或 API Key。

这批为预发布测试包，只采用本地签名（ad-hoc），**尚未使用 Apple Developer ID 签名，也未完成苹果公证**。macOS 可能拦截首次打开；确认下载来源及 SHA-256 后，可查看“系统设置 → 隐私与安全性”的“仍要打开”入口。无需关闭整个系统的安全保护。

仍需普通 Mac 用户测试输入法、Finder 下载后的首次打开、长时间写作和真实模型服务。云端自动检查通过不等于完成所有真机验收。

- [Mac 安装、Windows 数据迁移与源码构建说明](https://github.com/Daizhdd/xiedao/blob/main/docs/MACOS.md)
- [两个 Mac 架构的成功构建记录](https://github.com/Daizhdd/xiedao/actions/runs/37223167508)
- [Windows 回归记录](https://github.com/Daizhdd/xiedao/actions/runs/37223167620)
- [反馈问题](https://github.com/Daizhdd/xiedao/issues)

随包提供各架构的 SHA-256 校验文件、构建信息及包审计报告。软件免费，模型服务由用户配置，服务商可能收费。Windows 稳定版继续保留在原 Release。
