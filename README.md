# Raymond Tools

一组在本机运行的实用工具。各工具提供独立的启动入口、依赖说明和使用文档。

## 工具列表

| 工具 | 功能 | 运行依赖 | 文档 |
| --- | --- | --- | --- |
| `video-tool` | 视频直链下载、批量任务、HLS 分片缓存与失败续传 | Python 3.10+、FFmpeg / ffprobe | [使用说明](video-tool/README.md) |

## 快速开始

安装视频工具所需的 Python 和 FFmpeg 后，运行：

```bash
git clone https://github.com/clarence-wzy/raymond-tools.git
cd raymond-tools/video-tool
python3 app.py
```

Windows 使用 `python app.py`。也可以使用工具目录中的 `start.command`（macOS）、`start.bat`（Windows）或 `start.sh`（Linux）启动。

默认访问地址为 `http://127.0.0.1:8766`，实际端口以终端输出为准。安装步骤、命令行参数和支持范围见[视频工具文档](video-tool/README.md)。

## 项目结构

```text
raymond-tools/
  .github/workflows/    自动化检查
  video-tool/          视频下载工具
  CONTRIBUTING.md      贡献指南
  SECURITY.md          安全与隐私说明
  LICENSE              MIT 许可证
```

各工具的本机配置、缓存和输出文件由 `.gitignore` 排除。通用设置示例随源码提供，具体配置方式见相应工具的文档。

## 开发与贡献

- [贡献指南](CONTRIBUTING.md)
- [视频工具开发与测试](video-tool/docs/DEVELOPMENT.md)
- [维护与发布](video-tool/docs/RELEASING.md)
- [安全与隐私](SECURITY.md)

新增工具请使用独立子目录，并在工具列表中登记用途、依赖和文档入口。

## 许可证

除另有声明的第三方代码外，本仓库自有代码采用 [MIT License](LICENSE)，版权署名为 Raymond.Wu。第三方依赖使用各自的许可证。
