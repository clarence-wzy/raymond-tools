# 开发与测试

## 代码结构

```text
app.py                  本机 HTTP 入口、路由与页面资源白名单
check_env.py            Python / FFmpeg / 下载目录检查
shared/                 工具导航与公共页面
video_downloader/
  api.py                任务、命名、配置与来源索引
  core.py               FFmpeg 下载、进度、取消、合并与校验
  hls.py                HLS 分片下载、检查点与本地清单
  network.py            请求头来源策略与重定向检查
  privacy.py            诊断信息脱敏
  scheduler.py          任务并发和等待队列
  config.py             本机配置
  locator.py            FFmpeg / ffprobe 发现
  folder_picker.py      系统文件夹选择窗口
  system_proxy.py       系统代理提示
  web/                  原生 HTML / CSS / JavaScript 界面
  __main__.py           Python CLI
  cli/resume_dl.sh       历史参考入口
tests/                  Python 与 Node.js 测试
```

运行依赖只有 Python 标准库和外部 FFmpeg / ffprobe。Node.js 22 用于前端逻辑测试，不是应用运行依赖。

## 本地验证

在 `raymond-tools/video-tool` 目录执行；如果当前位于主仓库根目录，先运行 `cd video-tool`：

```bash
python3 -m unittest discover -s tests -v
node --test tests/test_web.cjs
node --check video_downloader/web/app.js
node --check shared/app.js
python3 -m compileall -q app.py check_env.py video_downloader tests
bash -n start.sh
bash -n start.command
bash -n video_downloader/cli/resume_dl.sh
```

Windows 将 `python3` 换成 `python`；Shell 语法检查使用 Bash。

HLS 集成测试只访问临时本机 HTTP 服务，视频由 FFmpeg 合成，不依赖真实站点、私人 Cookie 或视频素材。需要 PATH 中有 ffmpeg / ffprobe 和可用的 libx264 编码器；找不到工具时会跳过集成测试，请检查测试输出，不能把跳过当作下载验证通过。

安全回归测试覆盖同源和跨源资源、重定向、显式可信来源、HTTPS 降级、日志与异常信息脱敏、兼容回退保护。文件复用测试覆盖修改、清空、删除及重载索引后的行为，确认保留已有文件。

主仓库的 [`.github/workflows/video-tool.yml`](../../.github/workflows/video-tool.yml) 将工作目录设为 `video-tool`，在 Linux、macOS、Windows 上执行 Python 3.10 / 3.14 的无 FFmpeg 测试，以及 Node.js 逻辑和语法检查。CI 不安装或分发 FFmpeg；完整媒体集成验证需要在配有 FFmpeg 的本地环境运行上述完整测试。运行记录见 [GitHub Actions](https://github.com/clarence-wzy/raymond-tools/actions)，检查结果以对应提交为准。

CI 的 Python 入口为 `python3 -m tests.run_ci`，只使用标准库；失败时将测试名称和堆栈写入 GitHub 检查注释，便于定位平台差异。

仓库边界测试覆盖本机配置、缓存和原子写入临时文件的忽略规则。

## 开发约定

- 优先复用现有模块，保持标准库优先。
- 网络或诊断路径的改动要验证凭据不会跨源发送或进入日志。
- 使用合成地址、合成凭据和本机服务编写测试。
- 输出文件先写临时路径，校验成功后发布；避免覆盖用户已修改的文件。
- UI 当前使用 `textContent` / DOM API 渲染外部内容，保持这一方式。
- 修改功能时同步更新 README、配置示例和相关文档。
