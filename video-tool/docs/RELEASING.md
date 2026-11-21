# 维护与发布

## 仓库约定

默认分支为 `master`。各工具位于独立子目录，自动化检查统一配置在仓库根目录的 `.github/workflows/`。

新增工具时，请在根目录 README 的工具列表中登记，并提供启动入口、依赖说明、配置示例和测试。

## 版本控制范围

| 提交的内容 | 排除的本机文件 |
| --- | --- |
| 源码、页面资源、测试和文档 | `config.json`、`.task-index.json` |
| `config.example.json` 等通用示例 | `*.parts/` 缓存、AES 密钥、下载的视频 |
| 许可证及第三方声明 | 日志、环境变量文件、FFmpeg 二进制 |

根目录 `.gitignore` 定义通用规则，各工具的 `.gitignore` 定义工具专属规则。配置与索引的原子写入临时文件也应排除，例如 `config.xxxxxx.json` 和 `.task-index.xxxxxx`。

提交前，在仓库根目录检查：

```bash
git status --short --untracked-files=all
git diff --cached --check
git check-ignore -v video-tool/config.json video-tool/.task-index.json
```

忽略规则不影响程序读取本机文件，也不能清除已经提交到历史中的信息。凭据泄露后的处理见[安全说明](../../SECURITY.md)。

## 发布检查

1. 在 `video-tool` 目录运行[开发文档](DEVELOPMENT.md#本地验证)中的验证命令，确认媒体集成测试实际执行。
2. 确认对应提交的 GitHub Actions 检查通过。
3. 更新受影响的使用说明、支持范围和已知限制。
4. 检查提交文件、示例、截图和日志，使用合成或脱敏信息。
5. 保留根目录 MIT LICENSE 及适用的第三方版权和许可声明。

本工具外部调用用户自行安装的 FFmpeg。若分发包含 FFmpeg 的安装包，需要按具体构建核对 [FFmpeg 官方许可说明](https://ffmpeg.org/legal.html)；本仓库的 MIT LICENSE 不覆盖第三方软件。
