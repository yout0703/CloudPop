# CloudPop

**多网盘媒体桥接工具** — 将云盘视频转换为本地 `.strm` 文件，通过 Plex / Skybox VR 在 Quest 2 上播放。

> 当前版本支持 **115 网盘**。更多网盘（阿里云盘、百度网盘、OneDrive）可通过 `BaseProvider` 抽象类扩展接入。

---

## 为什么选 CloudPop？

| 问题 | AList 挂载 | CloudPop STRM |
|------|-----------|---------------|
| Plex 扫库触发大量 API 调用 | ✗ 每次文件读取都调用云盘 API | ✓ Plex 只看到本地 `.strm` 文本文件 |
| 风控 / 封号风险 | 高（Plex 缩略图生成） | 极低（仅播放时一次 API 调用） |
| 重启稳定性 | 挂载可能断开 | STRM 文件永久保留 |

---

## 快速开始

### 1. 安装

```bash
# 使用 uv（推荐）
cd /path/to/CloudPop
uv sync
```

或使用传统 pip：

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. 启动服务

```bash
uv run cloudpop serve
# Starting CloudPop v0.1.0 on http://127.0.0.1:19798
```

浏览器打开 `http://localhost:19798`，进入 Web 控制台。

### 3. 登录 115 网盘

**方式 A — 扫码登录（推荐）**

访问 `http://localhost:19798/login`，用 115 手机客户端扫码即可完成登录，Cookie 自动写入配置文件。

**方式 B — 手动填写 Cookie**

```bash
mkdir -p ~/.cloudpop
cp config/config.yaml.example ~/.cloudpop/config.yaml
```

编辑 `~/.cloudpop/config.yaml`，填入 Cookie（登录 115.com → F12 DevTools → Application → Cookies → 复制 `UID`、`CID`、`SEID`）：

```yaml
providers:
  "115":
    cookies:
      UID: "your_uid_here"
      CID: "your_cid_here"
      SEID: "your_seid_here"
```

验证是否生效：

```bash
uv run cloudpop auth check
# ✓ 认证成功 (115 cookie valid)
```

### 4. 生成 STRM 文件

**通过 Web UI（推荐）**：在控制台首页选择云盘目录，点击「生成 STRM」即可。

**通过 CLI**：

```bash
# 预览，不实际写入
uv run cloudpop generate --path "/Movies" --output ~/plex-media/Movies --dry-run

# 实际生成
uv run cloudpop generate --path "/Movies" --output ~/plex-media/Movies
```

### 5. 配置 Plex 媒体库

在 Plex → 设置 → 媒体库 → 添加媒体库：

| 媒体库 | 类型 | 根目录 |
|--------|------|--------|
| 电影 | 电影 | `~/plex-media/Movies` |
| 剧集 | 电视节目 | `~/plex-media/TV` |
| VR 视频 | 家庭视频 | `~/plex-media/VR` |

触发媒体库扫描，Plex 会立即识别到 `.strm` 文件。

### 6. 在 Skybox 上播放

Quest 2 打开 **Skybox VR** → 连接 Plex → 浏览 → 播放。

---

## Web UI

启动服务后访问 `http://localhost:19798`（局域网访问时替换为主机 IP）。

| 路径 | 页面 |
|------|------|
| `/` | 控制台主页：认证状态、扫描 & 生成 STRM、缓存管理 |
| `/login` | 115 二维码扫码登录 |

> 如需允许局域网访问，在 `config.yaml` 中设置 `server.public: true`，或启动时指定 `--host 0.0.0.0`。

---

## CLI 参考

```
cloudpop [--config <path>] [--verbose] <command>

serve                              启动 HTTP 代理服务器
  --host <host>                    覆盖监听地址
  --port <port>                    覆盖监听端口

auth check                         验证 115 Cookie 是否有效

scan --path /Movies                扫描云盘目录，列出视频文件
  --provider <name>                网盘提供商（默认：115）

generate --path /Movies            生成 STRM 文件
  --output <dir>                   本地输出目录（默认：配置文件中设定）
  --incremental                    仅处理新增 / 变更文件
  --cleanup                        删除孤立的 .strm 文件
  --dry-run                        预览，不实际写入
  --provider <name>                网盘提供商（默认：115）

cache clear                        清除所有缓存的下载 URL
cache stats                        显示缓存命中率

version                            显示版本号
```

---

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | Web 控制台首页 |
| `GET` | `/login` | 二维码登录页 |
| `GET` | `/health` | 健康检查 |
| `GET` | `/stream/115/{pickcode}` | 流媒体代理（Range 转发） |
| `POST` | `/api/auth/qr/start` | 申请新二维码 |
| `GET` | `/api/auth/qr/status/{uid}` | 轮询扫码状态 |
| `POST` | `/api/auth/qr/confirm` | 扫码成功后保存 Cookie |
| `GET` | `/api/auth/status` | 检查当前认证状态 |
| `GET` | `/api/folders` | 获取云盘目录列表 |
| `POST` | `/api/scan` | 扫描云盘目录 |
| `POST` | `/api/generate` | 生成 STRM 文件 |
| `DELETE` | `/api/cache` | 清除全部缓存 |
| `DELETE` | `/api/cache/{pickcode}` | 清除单个文件缓存 |

完整文档：`http://localhost:19798/docs`（OpenAPI / Swagger UI）

---

## VR / 360° 文件命名

Skybox 根据文件名关键字自动识别 VR 类型，**CloudPop 保留原始文件名**：

| 关键字 | 类型 |
|--------|------|
| `_360` | 360° 等矩形投影 |
| `_180` | 180° VR |
| `_SBS` / `_HSBS` | 左右并排 3D |
| `_OU` / `_TB` | 上下并排 3D |

---

## 配置参考

完整示例见 `config/config.yaml.example`。

```yaml
server:
  host: "127.0.0.1"
  port: 19798
  public: false            # true 时监听 0.0.0.0（允许局域网访问）

providers:
  "115":
    cookies:
      UID: ""
      CID: ""
      SEID: ""
    user_agent: "Mozilla/5.0 ..."

strm:
  output_dir: ~/plex-media          # .strm 文件写入目录
  base_url: http://localhost:19798  # 写入 .strm 的代理 URL 前缀
  min_file_size_mb: 100             # 跳过小于此大小（MB）的文件，0 表示不过滤
  copy_subtitles: false             # 是否同步字幕文件
  scan_folder_id: "0"               # 扫描目标文件夹 ID（通过选择器写入）
  scan_folder_path: "/"             # 对应路径

cache:
  download_url_ttl: 3600            # 下载 URL 缓存时长（秒）
  file_info_ttl: 86400              # 文件信息缓存时长（秒）

log:
  level: "INFO"                     # DEBUG / INFO / WARNING / ERROR
  file: "~/.cloudpop/cloudpop.log"
```

---

## 架构说明

```
Quest 2 (Skybox) → Plex → CloudPop → 115 CDN
```

1. Plex 读取 `.strm` → URL `http://localhost:19798/stream/115/{pickcode}`
2. CloudPop 查 TTL 缓存，命中则直接返回 CDN 地址
3. 未命中：调用 `proapi.115.com/app/chrome/downurl` 获取 CDN 地址
4. CloudPop 将 Range 请求透明转发至 115 CDN
5. Plex 将视频流推送至 Skybox

---

## 开发

```bash
# 安装依赖（含开发工具）
uv sync

# 运行全部测试
uv run pytest -v

# 代码格式检查
uv run ruff check cloudpop tests
```

---

## FAQ

**Q: 播放启动有约 10 秒延迟？**  
A: 正常现象——Plex 在做本地文件 stat + 元数据查询。CloudPop 本身连接建立后响应在 500ms 以内。

**Q: Cookie 失效了怎么办？**  
A: 访问 `http://localhost:19798/login`，用手机扫码重新登录；或手动更新 `~/.cloudpop/config.yaml`。

**Q: 能否用 302 重定向代替代理？**  
A: 115 CDN URL 绑定请求 IP，Plex → Quest2 的直接 302 会返回 403。代理模式（默认）可规避此问题。

**Q: 如何在 NAS / 服务器上部署，供局域网访问？**  
A: 在 `config.yaml` 中设置 `server.public: true`（或 `server.host: "0.0.0.0"`），`strm.base_url` 改为主机的局域网 IP，如 `http://192.168.1.100:19798`。

---

## License

MIT

