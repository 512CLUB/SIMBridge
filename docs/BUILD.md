# 构建说明

本文说明如何从源码重新打包 macOS Apple Silicon / arm64 版本。

## 环境

- macOS
- Python 3.13
- Homebrew `libusb`
- Apple Silicon Mac 当前已验证

安装 `libusb`：

```bash
brew install libusb
```

## 本地运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python src/server.py --host 127.0.0.1 --port 8765
```

然后访问：

```text
http://127.0.0.1:8765/
```

## 打包 App

```bash
./scripts/build-macos-arm64.sh
```

构建脚本会从 `assets/sim_card_icon.png` 生成 macOS 多尺寸 `.icns`，并写入 App Bundle。

打包输出：

```text
dist/SIMBridge.app
```

如果 `libusb` 不在脚本默认搜索路径，可以显式指定：

```bash
LIBUSB_PATH=/absolute/path/to/libusb-1.0.dylib ./scripts/build-macos-arm64.sh
```

## 生成 DMG

```bash
./scripts/make-dmg.sh
```

输出：

```text
release/SIMBridge.dmg
release/SIMBridge.app.zip
release/CHECKSUMS.txt
```

## 注意事项

- 当前未做 Apple 签名和公证。
- 首次打开可能需要右键“打开”或在系统设置中允许。
- 若要构建 Intel 或 Universal 版本，需要准备对应架构的 Python、PyObjC/WebKit 依赖和 `libusb`。
