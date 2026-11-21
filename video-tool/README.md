# Video Tools

一个在本机运行的视频直链下载工具。使用 Python 标准库提供 Web 界面，调用本机 FFmpeg 完成视频处理，无需安装第三方 Python 包。

适合批量保存你有权下载的 HLS 点播、MP4 和 MPEG-TS 视频。需要提供视频资源地址；暂不支持从普通网页自动解析视频。

本工具属于 [Raymond Tools](../README.md) 工具集。以下命令均在 `video-tool` 目录中执行。

## 功能

- 本地 Web 界面，也可通过命令行使用。
- 批量下载、统一前缀或逐个命名，同名文件自动避让。
- 任务队列、并发控制、取消和失败重试。
- HLS 点播按分片缓存，支持 AES-128 和失败续传；完整缓存可离线重新合并。
- 不适用原生 HLS 流程时，尝试 FFmpeg 时间续传。
- 合并到临时文件，通过可读性和时长检查后发布输出。
- 请求头按可信来源发送，日志隐藏 URL 路径、查询参数和已知凭据。

## 环境要求

- Python 3.10 或更新版本。
- 本机安装 `ffmpeg` 和 `ffprobe`，通常来自同一安装包。
- 程序目录和下载目录可写。
- Windows / Linux 的文件夹选择窗口需要 Tk；没有 Tk 时可手动填写目录。

当前完整测试环境为 macOS、Python 3.14.7、FFmpeg 9.0.1。旧 FFmpeg 可能缺少兼容下载所需选项；Windows / Linux 提供启动支持，尚未完成端到端实机验证。

从 [Python 官网](https://www.python.org/downloads/) 和 [FFmpeg 官网](https://ffmpeg.org/download.html) 安装所需工具。常见安装方式：

```bash
# macOS（已安装 Homebrew）
brew install ffmpeg
```

```powershell
# Windows（已安装 WinGet）
winget install Gyan.FFmpeg
```

安装后确认 `ffmpeg -version` 和 `ffprobe -version` 都能执行，也可在页面中指定 FFmpeg 路径。

## 快速开始

在 `raymond-tools/video-tool` 目录运行：

```bash
python3 app.py
```

Windows 可使用 `python app.py`。也可双击 `start.command`（macOS）、双击 `start.bat`（Windows），或运行 `bash start.sh`（Linux）。

浏览器会打开本机页面，默认地址为 `http://127.0.0.1:8766`；端口被占用时会尝试后续端口，以终端输出为准。

1. 选择下载目录。
2. 填写视频直链，每行一个，单次最多 50 个。
3. 选择自动命名、统一前缀或逐个命名。
4. 点击“开始下载”，在任务卡中查看进度、取消或重试。

页面“更多设置”提供请求头、代理、并发和严格校验选项。按 `Ctrl+C` 关闭服务。

```bash
# 指定端口，不自动打开浏览器
python3 app.py --port 9000 --no-browser

# 命令行下载示例；example.com 为占位地址
python3 -m video_downloader "https://example.com/index.m3u8" "video.mp4"

# 查看全部参数
python3 -m video_downloader --help
```

## 支持范围

| 输入 | 处理方式与边界 |
| --- | --- |
| 静态 TS HLS 点播 | 原生分片缓存、并发下载、AES-128；多码率清单选择最高声明带宽 |
| MP4 / MPEG-TS 直链 | FFmpeg 兼容流程，续传效果取决于源站和格式 |
| 独立音轨、fMP4、字节范围、直播清单 | 尝试兼容流程，无法保证完整缓存与续传 |
| 普通网页地址、平台页面解析、DRM 授权获取 | 不支持 |

原生 HLS 只向输入地址的同源站点发送自定义请求头，包括 Cookie、Authorization。跨站 CDN 需要在本机配置中明确添加可信来源；重定向也执行同样检查。

**兼容流程仅允许 `User-Agent`、`Accept`、`Accept-Language` 自定义头。** 携带 Cookie、Authorization、Referer 或其他自定义头的任务无法安全回退时会停止，并保留缓存。详见[配置说明](docs/CONFIGURATION.md#请求头与可信来源)。

任务历史目前保存在内存中，重启后不会恢复全部任务卡；来源索引与分片缓存可以继续复用。其他边界见[已知限制](docs/LIMITATIONS.md)。

## 文档

- [配置、代理和命令行](docs/CONFIGURATION.md)
- [缓存、续传和已知限制](docs/LIMITATIONS.md)
- [开发与测试](docs/DEVELOPMENT.md)
- [维护与发布](docs/RELEASING.md)
- [安全与隐私](SECURITY.md)
- [参与贡献](CONTRIBUTING.md)

## 许可与使用范围

本工具源码采用主仓库根目录的 [MIT License](../LICENSE)，版权署名为 Raymond.Wu。本仓库不包含 FFmpeg 二进制；FFmpeg 使用其自身许可证，参见 [FFmpeg 官方许可说明](https://ffmpeg.org/legal.html)。捆绑分发 FFmpeg 时，需要按具体构建核对许可证和对应源码要求。

请仅下载你拥有权利或已获得授权的内容，并遵守来源站点的使用条款。本工具不提供绕过 DRM 或获取未授权内容的功能。
