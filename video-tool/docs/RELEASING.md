# 仓库维护与发布准备

## 统一使用主仓库

日常开发、本地下载和后续开源统一使用 `raymond-tools` 主仓库，视频工具位于 `video-tool/`。后续工具放在同级目录，不在 `video-tool/` 内再建立 Git 仓库。

保留当前目录下的 `config.json`、`.task-index.json` 和缓存，原来的保存位置、个人设置与来源索引继续使用。公开源码通过 Git 的提交文件列表控制，不需要搬走本机数据或另维护一份发布副本。

在 `raymond-tools` 主仓库根目录检查：

```bash
git rev-parse --show-toplevel
git status --short --untracked-files=all
git check-ignore -v video-tool/config.json video-tool/.task-index.json
```

`--show-toplevel` 应指向 `raymond-tools` 自身，不能是存放其他私人项目的上级目录。GitHub Actions 工作流放在主仓库 `.github/workflows/`，视频工具工作流的运行目录为 `video-tool`。

## 本机数据保留与排除

| 内容 | 本地使用 | 提交到主仓库 |
| --- | --- | --- |
| 代码、页面、测试、文档、许可证 | 保留 | 提交 |
| `config.example.json` | 通用配置示例 | 提交 |
| `config.json` | 当前电脑的下载目录和个人设置 | 忽略 |
| `.task-index.json` | 当前电脑的下载来源索引 | 忽略 |
| `*.parts/` | 分片、AES 密钥和续传缓存 | 忽略 |
| 视频文件、日志、FFmpeg 二进制 | 按本机用途保留 | 忽略 |

配置和索引的原子写入临时文件也在忽略范围内，例如 `config.xxxxxx.json` 和 `.task-index.xxxxxx`；`config.example.json` 作为通用示例正常提交。

主仓库 `.gitignore` 管理通用本机文件；`video-tool/.gitignore` 管理本工具的配置、索引、媒体和缓存。忽略规则不会删除这些文件，程序仍可正常读写。

`.gitignore` 不能清除已被 Git 跟踪的敏感文件或历史。若历史曾提交真实凭据，应先撤销或轮换凭据，再处理历史；仅增加忽略规则不能使已泄露的凭据恢复安全。

## 公开前核对

- 从主仓库检查待提交文件列表，确认没有其他私人项目或本机数据。
- 在 `video-tool/` 运行完整测试，确认 FFmpeg 集成测试实际执行。
- 检查文档中的链接、路径与示例，截图和日志只使用合成或脱敏信息。
- 保留主仓库根目录的 MIT LICENSE（Raymond.Wu），以及适用的第三方版权和许可声明。
- 发布说明如实列出测试环境和未验证的平台。

本工具只外部调用用户自行安装的 FFmpeg。若以后捆绑分发 FFmpeg，应按具体构建重新核对 [FFmpeg 官方许可说明](https://ffmpeg.org/legal.html)；项目自身的 MIT LICENSE 不覆盖第三方软件。
