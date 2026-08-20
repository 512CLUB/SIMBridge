# SIMBridge v1.0.0 macOS 发布说明

## 安装包

- `SIMBridge.dmg`：推荐给普通用户使用。
- `SIMBridge.app.zip`：备用 App 压缩包。
- `CHECKSUMS.txt`：SHA-256 校验值。

## 功能

- App 内窗口渲染短信面板。
- 支持模块短信读取、发送、删除。
- 支持全部、收件、已发、未读筛选。
- 支持 LTE 信号和基站信息展示。
- 新增开发者模式，高级诊断功能和原始调试数据默认锁定。
- 新增短信邮件转发，可配置 SMTP、STARTTLS 或 SSL/TLS，并使用 macOS 钥匙串保存授权码。
- App 图标更新为 SIM 卡图标。
- 支持开机自启动开关。
- 支持普通手机号、服务号和短号。

## 已知限制

- 当前未做 Apple 签名和公证。
- 当前构建面向 macOS Apple Silicon / arm64。
- 当前短信发送按单条 UCS2 短信处理，内容建议控制在 70 个中文字符以内。

## License

This project is distributed under the custom `SIMBridge Non-Commercial Attribution License 1.0`.

Non-commercial use, modification, and redistribution are allowed with attribution. Commercial use is prohibited without prior written permission from the copyright holder.
