# SIMBridge

一款面向大疆第一代 4G 模块的 macOS 本地短信面板。

近日，二手平台上出现了不少价格约 50 元的大疆 4G 模块。它们用于大疆设备时，服务到期后通常需要续费；但在阅读[这篇文章](https://mp.weixin.qq.com/s/7sGT8gFRzTpkVfrZz3FVQQ)后，我发现模块除了提供 4G 网络，还具备短信收发能力，于是通过 Web Coding 完成了 SIMBridge。

项目从七月初立项，作者当时还是一名 16 岁的学生。经过持续打磨，SIMBridge 已经可以在 Mac 上完成短信收发、长期存档、邮件转发和手机端访问。如果你有更好的想法，欢迎前往 [Issues](https://github.com/512CLUB/SIMBridge/issues) 提出建议。

> SIMBridge 是非官方第三方项目，与 DJI、Quectel、运营商不存在隶属、授权或合作关系。

## 项目特点

1. **即装即用**：普通用户无需部署服务器、数据库或前端环境。
2. **Mac 同时作为服务端和客户端**：适合长期运行在 Mac mini 或其他 Mac 上，收到的短信可自动归档，并可在同一 Wi-Fi 下通过手机访问。
3. **本地优先**：短信、号码、备注和配置保存在本机；程序不会主动把数据上传到云端。
4. **不只收发短信**：支持搜索、收藏、备注、LTE 信号信息、开机自启动和 SMTP 邮件转发。

后续计划适配群晖 NAS、飞牛 NAS 和 Windows，当前仓库暂时只发布 macOS Apple Silicon / arm64 版本。

## 功能概览

- 查看模块、SIM、运营商、网络制式和短信存储状态。
- 读取、发送和删除短信，支持全部、收件、已发、未读和收藏筛选。
- 按号码、短信内容或备注搜索长期归档。
- 为短信添加备注或收藏标记。
- 通过 SMTP、STARTTLS 或 SSL/TLS 自动转发新短信。
- 在开发者模式中查看 LTE 频段、Cell ID、PCI、EARFCN、TAC、RSRP、RSRQ、RSSI、SINR 和原始 PDU。
- 设置登录后自动启动。
- 将 Mac 作为局域网短信服务器，供同一 Wi-Fi 下的手机查看或发送短信。

## 前期准备

### 硬件

- 一台 Apple Silicon Mac（M1、M2、M3、M4 或更新的 Apple 芯片）。
- 大疆第一代 4G 模块，常见型号为 Quectel EG25-G / Baiwang QDC507。
- 一张已激活、可正常收发短信的 nano-SIM 卡。
- 一根支持数据传输的 USB-C 线缆。

### 系统

- macOS 13 Ventura 或更新版本。
- 当前发行包仅提供 Apple Silicon / arm64 构建，Intel Mac 尚未发布和验证。
- 安装包已携带运行所需的 Python、`pyusb`、`pywebview` 和 `libusb`，普通用户无需安装开发环境。

## 检查并切换 4G 模块模式

SIMBridge 能识别以下两组常见 USB 标识：

- 大疆 / Baiwang 原始标识：`2ca3:4006`
- Quectel EC25 标准标识：`2c7c:0125`

先连接模块，在“终端”中检查 USB 设备：

```bash
system_profiler SPUSBDataType | grep -A 8 -E "BAIWANG|DJI|Quectel|2ca3|2c7c"
```

如果 SIMBridge 已经显示模块在线，不需要修改模式、VID/PID 或固件。

SIMBridge 需要模块处于可访问 AT 管理接口的短信/管理模式。如果模块此前被切换成 USB 网卡模式，且 SIMBridge 无法识别，可按照[参考文章](https://mp.weixin.qq.com/s/7sGT8gFRzTpkVfrZz3FVQQ)准备兼容的 AT 工具，然后依次发送：

```text
AT+QCFG="usbnet",0
AT+CFUN=1,1
```

第一条指令将模块恢复到 DJI 私有的管理模式，第二条指令重启模块使设置生效。重启时 USB 会短暂断开；请等待 5–10 秒，再重新打开或刷新 SIMBridge。

> **注意**
>
> - 模式设置会写入模块并在断电后保留，操作前请停止其他正在访问模块的软件。
> - 不要在 eSIM/Profile 写入期间切换模式或拔出模块。
> - 不建议仅为使用 SIMBridge 执行 `AT+QCFG="usbcfg",...` 永久改写 VID/PID；本项目已经兼容上述两组标识。
> - 不清楚 AT 指令作用时，请先在 Issues 中确认，避免照搬来源不明的刷机命令。

## 下载

当前仅发布 macOS Apple Silicon / arm64 版本：

- [下载 SIMBridge.dmg](https://github.com/512CLUB/SIMBridge/blob/main/release/SIMBridge.dmg)
- [下载 SIMBridge.app.zip](https://github.com/512CLUB/SIMBridge/blob/main/release/SIMBridge.app.zip)
- [查看 SHA-256 校验值](https://github.com/512CLUB/SIMBridge/blob/main/release/CHECKSUMS.txt)
- [打开完整 release 目录](https://github.com/512CLUB/SIMBridge/tree/main/release)

仓库目前为私有状态，下载前需要登录具备访问权限的 GitHub 账号。

下载后可以验证文件是否完整：

```bash
cd ~/Downloads
shasum -a 256 SIMBridge.dmg
```

将输出与 `CHECKSUMS.txt` 中 `SIMBridge.dmg` 对应的值比较；完全一致后再安装。

## 安装与首次启动

1. 打开下载的 `SIMBridge.dmg`。
2. 将 `SIMBridge.app` 拖到“应用程序”文件夹。
3. 连接已经插入 SIM 卡的 4G 模块。
4. 在“应用程序”中打开 SIMBridge。
5. 等待几秒，顶部状态显示“在线”后即可使用。

当前版本未使用 Apple Developer ID 签名和公证，首次运行时 macOS 可能提示“无法验证开发者”“Apple 无法检查其是否包含恶意软件”，或直接阻止启动。

推荐处理方法：

1. 在 Finder 的“应用程序”中找到 `SIMBridge.app`。
2. 按住 `Control` 点击应用，选择“打开”。
3. 在新的确认窗口中再次点击“打开”。
4. 如果仍被拦截，进入“系统设置 → 隐私与安全性”，在安全提示附近点击“仍要打开”。

如果系统仍提示应用已损坏，并且你确认文件来自本仓库且 SHA-256 校验一致，可以在“终端”执行：

```bash
xattr -dr com.apple.quarantine "/Applications/SIMBridge.app"
```

然后重新打开应用。不要对来源不明或校验不一致的程序移除隔离属性。

## 使用说明

### 收发短信

1. 打开“短信”页查看模块短信和本地归档。
2. 使用“全部 / 收件 / 已发 / 未读 / 收藏”筛选记录。
3. 在搜索框中按号码、内容或备注搜索。
4. 在右侧填写号码和内容，点击“发送短信”。
5. 检查确认窗口中的号码与内容，再确认发送。

中国大陆 11 位手机号会自动按 `+86` 处理，也支持服务号和短号。当前按单条 UCS2 中文短信处理，建议控制在 70 个中文字符以内。

部分模块不会保存已发短信，因此“已发”列表可能为空，这不代表发送失败。

### 配置 SMTP 邮件转发

在使用前，请先到邮箱服务商网页中开启 SMTP 服务。许多邮箱要求使用单独生成的“授权码”或“应用专用密码”，不能直接使用网页登录密码。

常见配置：

| 加密方式 | 常用端口 | 说明 |
| --- | ---: | --- |
| STARTTLS | `587` | 推荐，连接后升级为加密通道 |
| SSL/TLS | `465` | 从连接开始即加密 |
| 无加密 | `25` | 不推荐，部分网络会封锁 |

配置步骤：

1. 在右侧设置区点击“短信邮件转发 → 配置”。
2. 填写 SMTP 服务器、端口和加密方式。
3. 填写 SMTP 用户名和密码/授权码。
4. 填写发件邮箱、一个或多个收件邮箱，以及可选的邮件主题前缀。
5. 点击“发送测试邮件”，确认收件箱能够收到测试邮件。
6. 勾选“启用自动邮件转发功能”，然后保存。

首次启用时，SIMBridge 会把模块中已有短信设为基线，只转发之后新收到的短信。软件关闭后不会主动检查新短信，因此建议同时开启“开机自启动”。SMTP 密码或授权码保存在当前用户的 macOS 钥匙串中，不会以明文写入转发配置文件。

### 手机连接

Mac 版 SIMBridge 可以作为局域网短信服务器。同一 Wi-Fi 下的手机可以查看长期归档、发送短信、搜索内容、添加备注、收藏或删除记录。

1. 在 Mac 端右侧“手机连接”中设置登录账号和至少 8 位密码。
2. 开启局域网访问，并记下页面显示的手机访问地址和 6 位配对码。
3. 在手机浏览器中打开该地址，先输入账号密码，再输入配对码。
4. iPhone 可以通过 Safari 的“分享 → 添加到主屏幕”创建快捷入口。

Mac、4G 模块和 SIMBridge 必须保持运行，手机才可以收发或刷新短信。请只在可信局域网中开启该功能。

### 信号与基站信息

开启“开发者模式”后，可以查看 LTE Band、Cell ID、eNodeB、Sector、PCI、EARFCN、TAC、RSRP、RSRQ、RSSI、SINR 和 SRXLEV 等信息，也可以展开短信原始 PDU 用于技术排障。

这些信息主要用于信号诊断。普通短信收发不需要开启开发者模式。

### 开机自启动

将 `SIMBridge.app` 放入“应用程序”文件夹后，可以在右侧开启“开机自启动”。程序会创建当前用户的 LaunchAgent：

```text
~/Library/LaunchAgents/com.wangquanrun.simbridge.login.plist
```

关闭开关会删除该启动项，不需要管理员权限。

## 疑难解答

### SIM 卡设置了 PIN 码

常见表现：

- 顶部状态中的 SIM 状态显示 `SIM PIN`，而不是 `READY`。
- 模块可以被识别，但无法注册运营商网络。
- 短信读取、发送或自动转发失败。

解决方法：

1. 将 SIM 卡暂时插入手机。
2. 输入正确 PIN 解锁。
3. 在手机的 SIM 卡安全设置中关闭“SIM PIN”锁定，或确认每次上电后的解锁方式。
4. 将 SIM 卡装回 4G 模块，重新连接并等待网络注册。

不要反复猜测 PIN。连续输错通常会触发 PUK 锁定；此时需要联系运营商获取 PUK 码。

### 打开软件后提示找不到模块

- 确认线缆支持数据传输，而不只是充电。
- 拔下模块，等待几秒后重新连接。
- 退出其他可能占用模块的 AT、串口或 USB 工具。
- 使用前文命令检查 USB 标识是否为 `2ca3:4006` 或 `2c7c:0125`。
- 如果模块被切换成网卡模式，请按“检查并切换 4G 模块模式”恢复管理模式。

### 模块在线但无法收发短信

- 确认 SIM 状态为 `READY`。
- 确认运营商已经注册，且套餐支持短信业务。
- 检查短信中心号码是否正常。
- 尝试给自己的手机号发送一条短文本。
- 如果是物联网卡，确认运营商是否限制点对点短信。

### 短信出现乱码

SIMBridge 已处理 UCS2 中文短信、常见 PDU 编码和长短信 UDH 分片。如果仍然乱码，请开启开发者模式，保留该短信的原始 PDU，并在 Issues 中反馈。

### 邮件测试失败

- 检查 SMTP 主机名、端口和加密方式是否匹配。
- 优先使用授权码或应用专用密码，而不是网页登录密码。
- 确认发件地址与 SMTP 账号满足邮箱服务商要求。
- 检查网络、防火墙和 VPN 是否阻止对应端口。
- 先关闭自动转发，使用“发送测试邮件”逐项排查。

### 手机无法连接 Mac

- 确认手机和 Mac 位于同一 Wi-Fi。
- 保持 SIMBridge 运行，并允许它接收系统防火墙的入站连接。
- 使用 Mac 端当前显示的访问地址，不要使用 `127.0.0.1`。
- 账号密码验证后，需要继续输入本次启动生成的 6 位配对码。
- 服务重启或修改账号后，需要重新登录和配对。

## 隐私与安全

- 默认桌面服务只绑定 `127.0.0.1`。
- 只有用户主动开启“手机连接”时，才会提供局域网访问。
- 短信归档默认保存在 `~/Library/Application Support/SIMBridge/messages.db`。
- 手机登录密码使用 PBKDF2-SHA256 加盐哈希保存，不存储明文。
- SMTP 密码或授权码保存在 macOS 钥匙串。
- 软件不会主动把短信、号码、信号或基站信息上传到云端。
- 发送短信前会显示确认窗口，避免误触发送。

## 从源码运行

```bash
brew install libusb
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./scripts/run-dev.sh
```

构建 macOS App 和安装包：

```bash
./scripts/build-macos-arm64.sh
./scripts/make-dmg.sh
```

详细说明见 [docs/BUILD.md](docs/BUILD.md)。

## 许可证

本项目采用自定义的 [SIMBridge Non-Commercial Attribution License 1.0](LICENSE)。

允许在非商业目的下使用、复制、学习、修改和重新发布，但必须保留原作者、项目名称和出处。未经版权持有人书面许可，不允许商业使用、付费分发、商业设备捆绑、商业服务集成或用于营利性生产环境。

由于包含“禁止商业使用”条款，本许可证不是 OSI 意义上的开源许可证。第三方组件仍分别遵循其自身许可证，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 项目结构

```text
.
├── assets/                   # App 图标源文件
├── docs/                     # 使用、构建和发布文档
├── release/                  # macOS 安装包和校验文件
├── scripts/                  # 本地运行、构建和 DMG 打包脚本
├── src/                      # Python 后端和 Web 前端
├── tests/                    # 自动化测试
├── LICENSE
├── README.md
├── requirements.txt
└── THIRD_PARTY_NOTICES.md
```

## 致谢

- [微信公众号参考文章](https://mp.weixin.qq.com/s/7sGT8gFRzTpkVfrZz3FVQQ)
- Quectel EG25-G / EC25 相关公开技术资料与社区项目
- 所有提交 Issues、测试硬件和改进建议的用户
