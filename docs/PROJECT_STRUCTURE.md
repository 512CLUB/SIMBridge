# 项目结构

```text
.
├── assets/                   # App 图标等静态源资源
├── docs/                     # 中文使用说明、构建说明、发布流程
├── release/                  # 当前发布资产
├── scripts/                  # 开发和打包脚本
├── src/                      # 应用源码
├── tests/                    # 不依赖真实模块的自动化测试
├── .gitignore
├── LICENSE
├── README.md
├── requirements.txt
└── THIRD_PARTY_NOTICES.md
```

## 源码

- `src/server.py`：本地 HTTP 服务、USB/libusb 通信、AT 指令、短信 PDU 编码/解码、信号/基站信息解析、开机自启动 LaunchAgent 管理。
- `src/forwarding.py`：短信邮件转发配置、macOS 钥匙串凭据、SMTP 发送、轮询与去重状态。
- `src/launcher.py`：macOS App 启动器，创建 WebView 窗口并启动本地服务。
- `src/static/`：前端界面。
- `assets/sim_card_icon.svg`、`assets/sim_card_icon.png`：macOS App 图标源文件。

## 发布资产

- `release/SIMBridge.dmg`：推荐给用户安装。
- `release/SIMBridge.app.zip`：备用 App 压缩包。
- `release/CHECKSUMS.txt`：SHA-256 校验。
- `release/RELEASE_NOTES.md`：当前版本说明。

## 文档

- `README.md`：GitHub 首页说明。
- `docs/USER_GUIDE.zh-CN.md`：中文用户手册。
- `docs/BUILD.md`：构建说明。
- `docs/RELEASE.md`：发布流程。
