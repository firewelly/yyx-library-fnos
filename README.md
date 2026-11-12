# YYX书库 —— 飞牛 fnOS 版（.fpk 打包工程）

在飞牛 fnOS 上以应用中心 `.fpk` 形态运行 **YYX书库**（novelmgt，跨平台书库管理
与阅读器）。主项目见 **[firewelly/yyx-library](https://github.com/firewelly/yyx-library)**
（Flutter 桌面端 + Web 端）与 [firewelly/Novel_scraber](https://github.com/firewelly/Novel_scraber)。

> 独立维护的 fnOS 适配仓库（打包源码镜像自主仓库 `tool/fnos/`）。

## 架构（单文件直连）

- **书库（只读）**：浏览器内 SQLite WASM 经 HTTP Range 按需读取 NAS 上的
  `novelmgt.db`，服务端只是静态文件服务器（python3 标准库），无后端 API
- **阅读状态（可写）**：进度/书签/笔记走 `/api/state/*` JSON API，存独立状态库
  `novelmgt_state.db`——跟部署走，所有浏览器/设备共享
- fnOS 桌面/手机 App 经 iframe 打开入口（端口 **12702**），鉴权由 fnOS 用户体系承担

## 快速开始

**打包**（开发机）：

```bash
cd novelmgt_flutter
flutter build web --release
python tool/fnos/build_fpk.py       # 产物 dist/yyx-library-fnos-<ver>.fpk
```

**安装**（fnOS 应用中心 → 手动安装，或 `appcenter-cli install-fpk`）：

- 向导两项（留空用默认值）：
  - **书库文件绝对路径**：默认 `/mnt/dx4600/hermes3/novelmgt/novelmgt.db`
  - **状态库文件绝对路径**：默认应用数据目录（升级保留）
- 路径持久化在 `/var/apps/novelmgt/var/{db,state}.conf`，可手改后重启应用
- 桌面「YYX书库」图标 → 端口 12702

## 隐私与许可

BSD 3-Clause —— 见 [LICENSE](LICENSE)；隐私政策见 [PRIVACY.md](PRIVACY.md)。
书库数据只在本地 NAS 与浏览器间流动，无第三方上传。
