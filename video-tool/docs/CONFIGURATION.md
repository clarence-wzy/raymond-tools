# 配置说明

## 本机配置

程序读取项目根目录的 `config.json`；没有此文件时使用内置默认值。可参考 `config.example.json`。页面保存设置时会生成或更新配置文件；手动编辑后请在没有运行任务时重启程序。

`config.json`、`.task-index.json`、下载文件和缓存属于本机数据，不应提交到公开仓库。

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `ffmpeg_path` | 空 | FFmpeg 可执行文件或所在目录；空则自动发现 |
| `output_dir` | 用户下载目录 | 输出目录，建议使用绝对路径 |
| `port` | 8766 | 首选本机端口 |
| `stall_timeout` | 60 | 兼容流程无进展超时秒数；原生请求超时不超过 15 秒 |
| `max_retries` | 10 | 兼容流程连续失败次数、原生分片最大尝试次数 |
| `safety_seconds` | 2.0 | 兼容续传向前回退的秒数 |
| `keep_parts` | false | 成功后是否保留缓存 |
| `allow_local_input` | false | Web API 是否允许 `file://` 输入 |
| `max_concurrent` | 2 | 同时运行任务数，范围 1–6 |
| `hls_workers` | 4 | 每个视频的分片并发数，范围 1–8 |
| `strict_errors` | true | 严格处理 FFmpeg 报告的媒体错误 |
| `native_hls` | true | 优先使用原生 HLS 分片缓存 |
| `trusted_header_origins` | [] | 可以接收自定义请求头的额外来源 |

FFmpeg 按手动路径、`FFMPEG_PATH`、系统 `PATH`、常见安装目录依次查找。`ffprobe` 优先取同目录，找不到时再查 `PATH`。

## 请求头与可信来源

原生 HLS 默认只向输入地址的同源资源发送用户填写的请求头。同源要求**协议、主机和端口全部相同**；清单、子清单、分片、密钥及每次重定向都会检查。其他来源不接收这些自定义头。

如果你确认某个 CDN 也应该接收同一组凭据，可以在本机 `config.json` 添加：

```json
{
  "trusted_header_origins": ["https://cdn.example.com"]
}
```

这是配置片段；其余字段可继续保留。只填写来源，不带路径、查询参数、账号或密码。非默认端口必须明确填写；不支持通配符或自动信任子域名。HTTPS 输入的凭据不会发送给 HTTP 资源，即使该 HTTP 来源在列表中。

可信列表允许发送**整组请求头**，请只添加确认可接收这些凭据的站点。输入地址发生跨站重定向不会自动扩大信任范围。配置不写入来源索引或分片记录。

FFmpeg 兼容流程不能使用应用的同源策略，因此仅允许 `User-Agent`、`Accept`、`Accept-Language`。如果任务需要 Cookie、Authorization、Referer 或其他头，请使用受支持的原生 HLS 点播格式。无法安全回退时任务会停止，不会静默去掉鉴权再下载。已有完整原生缓存仍可离线合并。

请求头每行填写 `Name: Value`。不允许覆盖 Host、Content-Length、Connection、Transfer-Encoding 或 Proxy-Authorization 等传输头。批量任务共用请求头；不要在同一批次混用不同账号或来源的私人凭据。

## 代理

- **跟随环境**：使用进程的 `http_proxy` / `HTTP_PROXY`。系统代理仅展示为提示，不会自动应用。
- **直连**：不使用应用层代理；系统 VPN / TUN 路由仍可能接管流量。
- **指定 HTTP 代理**：例如 `http://127.0.0.1:7890`；不支持 SOCKS 地址。指定代理时忽略环境中的 `NO_PROXY` 排除项。

代理测试只验证 TCP 端口连通性，不验证代理出口或分流规则。代理偏好保存在当前浏览器的 localStorage 中；代理地址若包含账号密码，也会随偏好保留在该浏览器中。

## 命令行

```bash
python3 -m video_downloader --help
python3 -m video_downloader "https://example.com/index.m3u8" "video.mp4" --keep-parts
python3 -m video_downloader "https://example.com/index.m3u8" "video.mp4" --proxy direct
python3 -m video_downloader "https://example.com/index.m3u8" "video.mp4" --proxy "http://127.0.0.1:7890"
```

常用参数：

| 参数 | 作用 |
| --- | --- |
| `--ffmpeg PATH` | 手动指定工具路径 |
| `--headers TEXT` | 请求头，多个头使用实际换行分隔 |
| `--trusted-header-origin ORIGIN` | 为当前任务添加可信来源，可重复使用；叠加本机配置 |
| `--hls-workers N` | 分片并发数 |
| `--legacy` | 使用兼容流程，受请求头限制 |
| `--tolerant` | 允许 FFmpeg 容错，仍做时长检查 |
| `--restart` | 删除目标的已有缓存，从头下载，成功后可覆盖输出文件 |
| `--keep-parts` | 成功后保留缓存 |

普通下载和重试会保留同名文件。Web 任务卡上的“重新开始”需要确认目标路径，然后清除分片缓存并从头下载。下载通过校验后才会替换目标文件。CLI 使用 `--restart` 显式执行同样的操作。

私人请求头建议通过本机 Web 页面输入，避免出现在 Shell 历史或进程参数中。不要把真实签名链接、Cookie 或 Token 放进命令示例、脚本、Issue 或公开日志。
