# fnOS (misc) 部署说明 — novelmgt.fpk

单文件直连架构：
- **书库（只读）**：浏览器内 SQLite WASM 经 HTTP Range 按需读取 NAS 上的
  `novelmgt.db`，服务端只是静态文件服务器（python3 标准库），无后端 API。
- **阅读状态（可写）**：进度/书签/笔记走服务端 `/api/state/*` JSON API，
  存独立的状态库 `novelmgt_state.db`——**跟部署走，所有浏览器/设备共享，
  不按浏览器隔离**。fnOS 桌面/手机 App 经 iframe 打开入口，鉴权由 fnOS
  用户体系承担。

## 构建（开发机 Windows）

```bash
cd novelmgt_flutter
flutter build web --release
python tool/fnos/build_fpk.py
# 产物: dist/novelmgt-fnos.fpk
```

## 安装（misc, fnOS）

1. 把 `dist/novelmgt-fnos.fpk` 拷到 misc（scp/U盘均可）。
2. fnOS 桌面 → 应用商店 → 右上角"手动安装"（或 SSH：
   `appcenter-cli install-fpk /path/to/novelmgt-fnos.fpk`）。
3. 安装向导两项配置（都留空用默认值）：
   - **书库文件绝对路径**：默认 `/mnt/dx4600/hermes3/novelmgt/novelmgt.db`
     （NFS 挂载的 dx4600 共享库）；dx4600 本机部署填
     `/volume3/hermes3/novelmgt/novelmgt.db`。
   - **状态库文件绝对路径**：默认 `/var/apps/novelmgt/var/novelmgt_state.db`
     （应用数据目录，升级保留）。路径持久化在
     `/var/apps/novelmgt/var/{db,state}.conf`，可随时手改后重启应用。
4. 桌面出现"小说管理"图标，点开即用（入口 `/index.html?nas=1`，端口 12702）。

## dx4600 (UGOS Pro) 部署

> DXP4800 (UGOS Pro) 的**一键部署工程**见 `tool/ugos/`（2026-09-04 已部署，
> 含 UGOS Pro SFTP 残缺 / WiFi 分块传输的完整处理）；下面的 compose 示例
> 适用于 dx4600 本机部署。

同一架构，UGOS Docker 里跑（注意 bin 也要挂载）：

```yaml
services:
  novelmgt:
    image: python:3.12-alpine
    volumes:
      - ./ui:/srv/ui:ro
      - ./bin:/srv/bin:ro
      - /volume3/hermes3/novelmgt/novelmgt.db:/srv/db/novelmgt.db:ro
      - /volume3/hermes3/novelmgt/state:/srv/state
    command: >
      python3 /srv/bin/novel_webserver.py --web-dir /srv/ui
      --db /srv/db/novelmgt.db --state-db /srv/state/novelmgt_state.db
      --port 12702
    ports: ["12702:12702"]
    restart: unless-stopped
```

## 更新书库

桌面端导入新小说后：

```bash
python scrapers/upload_novel_db_to_dx4600.py   # 快照 + SFTP 上传（断点续传、幂等）
```

dx4600 上的库更新后，misc 经 NFS 直接可见（无需重启服务，刷新页面即可）。
状态库与书库相互独立，更新书库不影响阅读状态。

## 架构与限制

- 书库只读：NAS 端永不写书库文件；**进度/书签/笔记在服务端状态库**
  （`--state-db` 指定，fnOS 默认在应用 var 目录），同部署下跨浏览器共享。
  多台 NAS 各有独立状态库（跨 NAS 状态同步是未来功能）。
- 若把状态库指到 dx4600 共享目录供多台 NAS 共用：SQLite 经 NFS 并发写有
  损坏风险，只建议单台 NAS 写入时这么做。
- 书名/作者/简介/章节标题搜索可用；章节标题搜索首次会顺序扫描标题索引区
  （约 28MB），LAN 上十几秒，之后走内存缓存。
- 库的索引已含 books(createdAt/rating/wordCount)、book_tags(tagId/bookId)，
  排序/筛选均为索引查询。**切勿删除这些索引**，否则排序会退化为全表扫描
  （经 HTTP 不可用）。
- SQLite 库不能经 NFS 双机并发写：书库永远只读，唯一写入方是桌面端
  （本机库）→ 快照上传 → dx4600。
- 状态 API 无鉴权（局域网/飞牛远程中转网关之后）；如暴露公网需自行加反代鉴权。
