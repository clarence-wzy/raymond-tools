# 参与贡献

每个工具保存在独立子目录中。修改已有工具前，请阅读该工具的使用、开发和安全说明，并保持改动聚焦。

当前视频工具的贡献与测试要求见：

- [video-tool/CONTRIBUTING.md](video-tool/CONTRIBUTING.md)
- [video-tool/docs/DEVELOPMENT.md](video-tool/docs/DEVELOPMENT.md)

新增工具时：

1. 添加独立目录、README、运行入口和配置示例，在主 README 的工具表中登记。
2. 为本机配置、凭据、缓存和输出文件提供 `.gitignore`，不要提交私人数据。
3. 按需添加测试，并将 GitHub Actions 工作流放在主仓库 `.github/workflows/` 下，设置正确的工具工作目录。
4. 说明运行依赖、已验证平台和适用的许可声明；保留第三方代码的版权与许可证。

仓库只在 `raymond-tools` 根目录维护一个 Git 边界，不在各工具内创建嵌套仓库。问题报告和测试素材请使用合成地址、凭据与视频，不要提交真实账号、签名链接或私人内容。
