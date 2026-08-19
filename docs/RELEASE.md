# 发布流程

1. 确认源码改动已完成。
2. 更新 `README.md` 和 `docs/USER_GUIDE.zh-CN.md`。
3. 执行：

```bash
./scripts/build-macos-arm64.sh
./scripts/make-dmg.sh
```

4. 检查 `release/CHECKSUMS.txt`。
5. 在 GitHub 创建 Release。
6. 上传以下文件作为 Release Assets：

```text
release/SIMBridge.dmg
release/SIMBridge.app.zip
release/CHECKSUMS.txt
```

## 当前 Release 内容

- App 内 WebView 窗口渲染，不再自动打开系统浏览器。
- 支持短信查看、发送、删除和筛选。
- 支持信号/基站信息查看。
- 支持开机自启动开关。
- 支持服务号/短号收件号码。
- DMG 内包含 `SIMBridge.app`、`README.md` 和“应用程序”拖拽入口。
