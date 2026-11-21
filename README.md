# Raymond Tools

个人工具集合。每个工具放在独立子目录中，分别提供运行入口、配置示例、使用说明和测试。

本地使用与开源维护使用同一个仓库。私人配置、下载索引、缓存和输出文件由各工具的 `.gitignore` 排除，不需要为日常开发再维护一份发布副本。

## 工具

| 工具 | 用途 | 运行依赖 | 文档 |
| --- | --- | --- | --- |
| `video-tool` | 本地视频直链下载、批量任务、HLS 缓存与续传 | Python 3.10+、FFmpeg / ffprobe | [使用说明](video-tool/README.md) |

## 开始使用视频下载工具

在主仓库目录执行：

```bash
cd video-tool
python3 app.py
```

Windows 可使用 `python app.py`。已有本机配置会继续生效，也可直接使用 `video-tool` 中的启动脚本。

首次使用请先安装依赖，详见 [video-tool/README.md](video-tool/README.md)。

## 仓库结构

```text
raymond-tools/
  .git/                 主仓库的本地 Git 数据
  .github/workflows/    各工具的自动化检查
  .gitignore            通用本机文件排除规则
  README.md
  LICENSE
  CONTRIBUTING.md
  SECURITY.md
  video-tool/
    .gitignore          视频工具的配置、索引、缓存和输出排除规则
    config.example.json 可提交的配置示例
    config.json         本机配置，保留但不提交
    .task-index.json    本机下载来源索引，保留但不提交
    app.py              视频工具启动入口
    README.md
```

新增工具时，在仓库根目录添加同级工具目录，并在上面的工具表中登记入口、用途和依赖。工具目录内不应初始化嵌套的 Git 仓库。

## 本机数据与 Git

`video-tool/config.json` 中的保存路径和个人设置、`.task-index.json` 中的下载来源索引、分片缓存、视频文件以及日志均不作为源码提交。`config.example.json` 保留通用默认值，可供其他使用者参考。

日常运行和修改代码继续在同一个 `video-tool` 目录完成。发布准备见[视频工具的仓库维护说明](video-tool/docs/RELEASING.md)。Git 忽略规则只影响提交候选文件，不会删除本机文件或阻止程序读取它们。

## 开发与贡献

- [贡献说明](CONTRIBUTING.md)
- [视频工具开发与测试](video-tool/docs/DEVELOPMENT.md)
- [安全与隐私](SECURITY.md)

实际 GitHub Actions 工作流位于主仓库的 `.github/workflows/`。目前配置视频工具的 Python 与前端检查；完整媒体集成测试需要本机 FFmpeg。

## 许可证

除另有声明的第三方代码外，仓库内自有代码统一使用根目录的 [MIT License](LICENSE)，版权署名为 Raymond.Wu。各工具保留第三方依赖说明。
