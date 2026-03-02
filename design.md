# CloudPop — 详细设计文档 v0.1

> 编写日期：2026-03-01  
> 版本：v0.1 MVP  
> 技术栈：Python 3.12 · FastAPI · httpx · uvicorn

---

## 目录

1. [背景与目标](#1-背景与目标)
2. [系统整体架构](#2-系统整体架构)
3. [完整播放链路](#3-完整播放链路)
4. [115 网盘接入设计](#4-115-网盘接入设计)
5. [STRM 文件生成设计](#5-strm-文件生成设计)
6. [流媒体代理设计](#6-流媒体代理设计)
7. [缓存层设计](#7-缓存层设计)
8. [Plex 集成设计](#8-plex-集成设计)
9. [VR / 360° 视频支持](#9-vr--360-视频支持)
10. [配置系统设计](#10-配置系统设计)
11. [API 接口规范](#11-api-接口规范)
12. [CLI 设计](#12-cli-设计)
13. [目录结构](#13-目录结构)
14. [错误处理规范](#14-错误处理规范)
15. [安全设计](#15-安全设计)
16. [性能目标](#16-性能目标)
17. [技术决策记录](#17-技术决策记录)

---

## 1. 背景与目标

### 1.1 使用场景

用户在 115 网盘存储了大量视频（包括普通电影/剧集和 VR/360° 视频），希望：

- **不下载到本地**，直接通过 Plex Media Server 播放
- 在 Quest 2 上使用 **Skybox VR Player** 连接 Plex，流畅播放 4K/VR 内容
- 网盘文件变化后能**自动同步**到 Plex 媒体库

### 1.2 为什么选择 STRM 方案而非 AList 挂载

社区中常见的另一种方案是用 AList 将网盘挂载为本地磁盘，然后直接在 Plex 中添加该目录。**CloudPop 刻意不采用这种方式**，原因如下：

| 问题 | AList 挂载方案 | CloudPop STRM 方案 |
|------|--------------|-------------------|
| Plex 刮削触发 API 调用 | Plex 在扫描、刮削、生成缩略图时会**大量读取文件**，每次读取都真实调用网盘 API | Plex 只看到本地 `.strm` 文本文件，刮削时**不访问网盘** |
| 网盘风控 | 高频 API 调用容易触发 115 风控，导致账号限速或封禁 | 网盘 API **仅在用户实际播放时**才被调用，调用频率极低 |
| 稳定性 | 挂载断开会导致 Plex 媒体库报错 | STRM 文件持久存在，服务重启不影响媒体库 |
| Plex 重复扫描 | 每次启动/定时扫描都会重新遍历网盘目录 | Plex 扫描的是本地文件系统，速度快且不消耗网盘配额 |

> **核心原则**：CloudPop 对网盘的访问做到最小化——只在用户按下播放键时才产生一次下载 URL 请求，其余所有 Plex 的管理操作（扫描、刮削、缩略图）均与网盘完全隔离。

### 1.3 核心约束

| 约束 | 说明 |
|------|------|
| 115 无官方开放 API | 使用 Cookie 鉴权 + 逆向工程的私有接口 |
| 下载链接有时效性 | 需要在到期前自动刷新，对 Plex 暴露**稳定永久地址** |
| Skybox 通过 Plex 协议访问 | CloudPop 无需直面 Quest 设备，只需 Plex 能正确读到 STRM |
| 部署在本地 Mac | 同机运行，低延迟，不需要公网穿透 |

---

## 2. 系统整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                         Quest 2 (Skybox)                         │
│                     Plex 客户端协议 (XML API)                    │
└─────────────────────────────┬────────────────────────────────────┘
                              │ HTTP / Plex API
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Plex Media Server (本地 Mac)                   │
│  ┌─────────────────────────────────────────────────────────┐     │
│  │  /mnt/plex/Movies/Avatar (2009)/Avatar.strm             │     │
│  │  /mnt/plex/VR/SomeSBSVideo_SBS.strm                    │     │
│  └─────────────────────────────────────────────────────────┘     │
└─────────────────────────────┬────────────────────────────────────┘
                              │ HTTP Range Request
                              │ GET /stream/115/{file_id}
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                   CloudPop Server (port 19798)                    │
│                                                                  │
│   ┌───────────────┐  ┌──────────────┐  ┌────────────────────┐   │
│   │ Stream Proxy  │  │STRM Generator│  │  Provider Manager  │   │
│   │  (FastAPI)    │  │   (CLI/API)  │  │                    │   │
│   └───────┬───────┘  └──────────────┘  └─────────┬──────────┘   │
│           │                                       │              │
│   ┌───────▼───────────────────────────────────────▼──────────┐   │
│   │                    Cache Manager (TTL)                    │   │
│   └───────────────────────────────┬───────────────────────────┘  │
└───────────────────────────────────┼──────────────────────────────┘
                                    │ HTTPS
                                    ▼
                    ┌───────────────────────────────┐
                    │        115 网盘 API            │
                    │  webapi.115.com / proapi.115  │
                    └───────────────────────────────┘
```

### 2.1 模块职责总览

| 模块 | 职责 |
|------|------|
| **Stream Proxy** | 接收 Plex 的 Range 请求，通过 115 API 获取真实下载 URL，转发流量 |
| **STRM Generator** | 遍历 115 指定目录，生成 `.strm` 文件到本地 Plex 媒体目录 |
| **Provider Manager** | 封装 115 API，提供文件列表、文件信息、下载 URL 查询等操作 |
| **Cache Manager** | 缓存下载 URL（TTL 可配置），避免频繁调用 115 API |

---

## 3. 完整播放链路

```
用户操作：Skybox → 选择 Plex 服务器 → 浏览媒体库 → 点击播放
                                    │
                                    ▼
         Plex 读取 STRM 文件内容 → http://localhost:19798/stream/115/{file_id}
                                    │
                                    ▼
         CloudPop 收到请求 → 查 Cache(file_id) → 未命中 → 调用 115 API
                                    │
                                    ▼
         115 返回时效性下载 URL → CloudPop 缓存 URL（3600s TTL）
                                    │
                                    ▼
         CloudPop 向 115 CDN 发起 Range 请求 → 透传响应给 Plex
                                    │
                                    ▼
         Plex 将流分发给 Skybox → VR 播放
```

> **关键点**：CloudPop 对 Plex 暴露的是**永久稳定的代理地址**，下载 URL 的刷新由 CloudPop 内部处理，Plex 和 Skybox 感知不到。

---

## 4. 115 网盘接入设计

### 4.1 认证方式

115 网盘目前没有对外开放的 OAuth 接口，认证方式为 **Cookie-based**。

需要以下 Cookie 字段（在浏览器登录后提取）：

| Cookie Key | 说明 |
|------------|------|
| `UID` | 用户标识 |
| `CID` | 会话标识 |
| `SEID` | 安全标识 |
| `KID` | 可选，部分接口需要 |

**提取方法**：登录 115.com → 浏览器开发者工具 → Application → Cookies → 复制上述字段。

### 4.2 关键 API 端点

> 注：以下为社区逆向工程整理的私有 API，不作任何官方保证。

#### 4.2.1 【主要方案】按类型搜索全量文件（推荐用于 STRM 生成）

> **核心优化**：115 提供按文件类型跨目录搜索的接口，可以一次性拉取指定根目录下**所有层级**的视频文件，无需逐目录递归。API 调用次数从 `O(目录数) × O(每目录页数)` 降至 `O(视频总数 ÷ 100)`。

```
GET https://webapi.115.com/files/search
    ?cid={folder_id}       # 根目录 ID（0 = 全盘）
    &type=4                # 4 = 视频类型（关键参数）
    &limit=100
    &offset={offset}       # 分页偏移
    &format=json
    &natsort=1
```

响应关键字段：

```json
{
  "data": [
    {
      "fid": "1234567890",     // 文件 ID
      "n":   "Avatar.mkv",    // 文件名
      "s":   4294967296,      // 大小（bytes）
      "pc":  "abc123pickcode", // pickcode（下载和生成 STRM 使用）
      "te":  1700000000,      // 修改时间戳（Unix）
      "cid": "9876543210",    // 所在文件夹 ID（用于还原路径）
      "pid": "9999999999"     // 父级文件夹 ID
    }
  ],
  "count": 500              // 视频文件总数（用于计算分页）
}
```

> **注意**：`type=4` 会自动筛选视频文件，无需在客户端手动过滤扩展名。

---

#### 4.2.2 目录树接口（用于还原路径结构）

搜索接口返回的每个文件仅包含其所在的 `cid`（文件夹 ID），还需要知道每个文件夹的完整路径才能生成正确的本地目录层级。115 提供专门的目录树接口：

```
GET https://webapi.115.com/files/getid
    ?path={url_encoded_path}   # 例：/Movies/2024
```

或批量获取文件夹信息：

```
GET https://aps.115.com/natsort/files.php
    ?cid={folder_id}
    &show_dir=1
    &limit=1
    &offset=0
    &format=json
```

响应中的 `path` 字段包含从根到当前文件夹的完整路径数组：

```json
{
  "path": [
    {"cid": "0",          "name": "根目录"},
    {"cid": "111111111",  "name": "Movies"},
    {"cid": "222222222",  "name": "Avatar (2009)"}
  ]
}
```

**实际使用方式**：

```
第一步：一次性拉取所有视频文件（搜索接口，分页）
          → 得到所有 (pickcode, cid, 文件名) 三元组
第二步：收集所有唯一的 cid 值
          → 批量查询每个 cid 的路径（可并发，每个 cid 一次请求）
第三步：组合路径 + 文件名 → 生成 STRM
```

> **调用次数估算**（以 1000 个视频文件、100 个不同文件夹为例）：
> - 旧方案（逐目录递归）：约 100 次 list 请求 + 翻页 ≈ 数百次
> - 新方案（搜索 + 路径查询）：10 次搜索分页 + 100 次路径查询 = 约 110 次，且路径查询只在首次生成时执行，后续增量更新只需搜索新文件

---

#### 4.2.3 【备用方案】逐目录列举文件

当搜索接口不可用或需要精确控制某个特定目录时，使用逐目录列举（分页遍历）：

```
GET https://webapi.115.com/files
    ?aid=1
    &cid={folder_id}       # 文件夹 ID，根目录为 0
    &o=file_name
    &asc=1
    &offset={offset}       # 分页偏移
    &limit=100
    &show_dir=1
    &natsort=1
    &format=json
```

---

#### 4.2.4 获取下载链接

> **重要发现**（来自 SheltonZhu/115driver 逆向）：115 的正式 App 下载 API 对请求体和响应体均使用 **m115 ECDH 加密**。有两种接入方式，v0.1 使用方式 A：

**方式 A — Web/Chrome 扩展 API（无加密，v0.1 采用）**

```
GET https://proapi.115.com/app/chrome/downurl
    ?pickcode={pickcode}
Headers:
    Cookie: UID=...; CID=...; SEID=...
    User-Agent: Mozilla/5.0 ...
```

响应：

```json
{
  "state": true,
  "data": {
    "{pickcode}": {
      "url": {
        "url": "https://cdnfhnfile.115.com/...?t=...sign=..."
      },
      "file_name": "Avatar.mkv"
    }
  }
}
```

**方式 B — App API（m115 ECDH 加密，更稳定，v0.2 升级方向）**

```
POST https://proapi.115.com/app/chrome/downurl
     Body: data={m115_ecdh_encrypted({pickcode: "xxx"})}
           t={timestamp}
```

- 请求和响应都用临时 ECDH 密钥加密/解密
- 参考实现：`SheltonZhu/115driver/pkg/crypto/m115`
- 115driver 还提供了 Android 端点（`AndroidApiDownloadGetUrl`），行为略有不同

> **注意**：返回的 URL 通常有 3600 秒时效，且绑定请求者 IP，需要在同一网络环境下使用。

#### 4.2.3 获取文件夹 ID（路径导航）

通过递归调用列举 API 以 `is_dir=1` 过滤，实现路径树导航。

### 4.3 Provider 接口设计

```python
# providers/base.py

from dataclasses import dataclass
from typing import Optional, Iterator

@dataclass
class FileInfo:
    id: str            # file_id (fid)
    pickcode: str      # 用于获取下载 URL 的 pick_code
    name: str          # 文件名（含扩展名）
    size: int          # 字节数
    is_dir: bool
    parent_id: str     # 父文件夹 ID
    modified_at: int   # Unix 时间戳


class BaseProvider:
    """所有网盘 Provider 必须实现的接口"""

    def authenticate(self) -> bool:
        """验证凭据是否有效，返回 True/False"""
        raise NotImplementedError

    def list_files(self, folder_id: str = "0") -> Iterator[FileInfo]:
        """列举指定文件夹下所有文件/子文件夹（自动分页）"""
        raise NotImplementedError

    def get_download_url(self, pickcode: str) -> str:
        """返回真实下载 URL（可能有时效性）"""
        raise NotImplementedError

    def get_file_info(self, file_id: str) -> Optional[FileInfo]:
        """返回单个文件的元信息"""
        raise NotImplementedError

    def find_folder_id(self, path: str) -> Optional[str]:
        """将路径字符串解析为 folder_id，例如 '/Movies/2024'"""
        raise NotImplementedError
```

### 4.4 115 Provider 实现要点

- **User-Agent 必须伪装**为浏览器，否则 API 返回 403
- 下载请求中需要携带 Cookie（即同 IP 限制）
- 分页：`limit=100`，通过 `offset` 翻页，直到 `offset >= count`
- pickcode 字段在文件列表响应中为 `pc`，注意和 `fid` 区分

---

## 5. STRM 文件生成设计

### 5.1 STRM 文件规范

STRM 文件是**纯文本文件**，内容仅一行 URL：

```
http://localhost:19798/stream/115/PICKCODE
```

> 使用 `pickcode` 而非 `file_id`，因为获取下载 URL 的接口需要 pickcode。

### 5.2 目录结构映射规则

```
115 网盘路径:                         本地 STRM 输出路径:
/Movies/Avatar (2009)/Avatar.mkv  →  {output}/Movies/Avatar (2009)/Avatar.strm
/VR/360Videos/Sunset_360.mp4      →  {output}/VR/360Videos/Sunset_360.strm
```

规则：
1. 完整保留目录层级
2. 文件扩展名替换为 `.strm`（文件名主体不变）
3. VR 标识（`_360`、`_180`、`_SBS`、`_OU` 等）**保留在文件名中**，供 Plex/Skybox 识别

### 5.3 生成流程（两阶段搜索方案）

传统递归遍历方案（AList 等工具的常见实现）每个文件夹都需要单独的 API 请求。CloudPop 采用**两阶段方案**，大幅减少 API 调用，降低触发 115 风控的风险：

```
阶段一：搜索全量视频文件（按类型搜索，自动跨目录）
─────────────────────────────────────────────
调用 /files/search?cid={root_id}&type=4
分页拉取，每页 100 条
收集所有 (pickcode, cid, name, modified_at) 四元组
                        │
                        ▼
阶段二：批量解析路径
─────────────────────────────────────────────
提取所有唯一的 cid 值（去重）
并发查询每个 cid 的路径（asyncio.gather，并发数 ≤ 5）
        → 构建 cid → full_path 映射表（缓存到本地）
                        │
                        ▼
阶段三：生成 STRM 文件
─────────────────────────────────────────────
对每个 (pickcode, cid, name)：
  构建本地路径 = output_dir + path_map[cid] + stem(name) + ".strm"
  内容 = {base_url}/stream/115/{pickcode}
  如果文件已存在且内容未变 → 跳过
  否则写入
                        │
                        ▼
完成，输出统计（created / skipped / errors）
```

**与旧方案的 API 调用对比（以 500 视频/50 目录为例）**：

| 方案 | API 调用次数 | 说明 |
|------|------------|------|
| 逐目录递归（旧） | ~150 次 | 50 目录 × 平均 3 页/目录 |
| 两阶段搜索（新） | ~55 次 | 5 页搜索 + 50 次路径查询（首次全量） |
| 增量更新（新） | ~5 次 | 仅搜索 `modified_at > 上次扫描时间` 的文件，路径 map 复用缓存 |

> 路径映射表（`cid_path_map`）在初次生成后持久化到 `~/.cloudpop/state.json`，增量更新时只需查询新出现的 `cid`，不重复请求已知路径。

### 5.4 视频文件识别

**主要方式**：使用 `type=4` 搜索参数，由 115 服务端直接筛选视频文件，无需客户端过滤。

**备用方式**（逐目录列举时）：本地按扩展名过滤：

```python
VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".mov", ".wmv",
    ".flv", ".m4v", ".ts", ".m2ts", ".rmvb",
    ".iso", ".bdmv"
}
```

### 5.5 增量同步

参考 SmartStrm 的实战经验，提供两种增量模式：

**模式 1：搜索时间过滤（默认，精准）**

- 调用 `/files/search?type=4&asc=0&o=user_utime`，只拉取 `modified_at > 上次扫描时间` 的文件
- 适合各种目录深度，无遗漏
- 路径映射 map 复用缓存，只查询新出现的 cid

**模式 2：目录修改时间检查（`--dir-time-check`，快速）**

- 遍历目录树时比较远端目录 `modified_at` 与本地记录
- 时间未变 → 跳过该目录及全部子目录，不产生任何 API 调用
- **115 的行为（已验证）**：新增或删除文件均会更新直接父目录的修改时间
- 局限：若内容变化在深层子目录，上层目录时间不变时会漏检（SmartStrm FAQ 有记录）

**清理孤立 STRM**：`--cleanup` 选项对比本地 STRM 与远端文件列表，删除已不存在于网盘的孤立 `.strm` 文件。

### 5.6 字幕文件同步

参考 SmartStrm 的 `copy_ext` 功能，生成 STRM 时可同步下载字幕文件到同级目录（Plex/Emby 自动识别同名字幕）：

```python
COPY_EXTENSIONS = {".srt", ".ass", ".ssa", ".sub", ".vtt"}
```

配置：`strm.copy_subtitles: true`（默认关闭）

### 5.7 文件大小过滤

参考 SmartStrm 的 `media_size` 选项，过滤小于阈值的文件，避免预告片、花絮生成无用 STRM：

```yaml
strm:
  min_file_size_mb: 100  # 默认 100MB，0 表示不过滤
```

---

## 6. 流媒体代理设计

### 6.1 端点规范

```
GET /stream/115/{pickcode}
```

| 参数 | 来源 | 说明 |
|------|------|------|
| `pickcode` | URL 路径 | 115 文件的 pickcode |

### 6.2 请求处理流程

```python
async def stream_endpoint(pickcode: str, request: Request):
    # 1. 从 Cache 获取真实 URL
    real_url = cache.get(pickcode)

    # 2. Cache 未命中 → 调用 Provider 获取
    if not real_url:
        real_url = await provider.get_download_url(pickcode)
        cache.set(pickcode, real_url, ttl=3600)

    # 3. 提取客户端的 Range 头
    range_header = request.headers.get("Range")

    # 4. 使用 httpx 向 115 CDN 发起请求（携带 Range）
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            real_url,
            headers={"Range": range_header} if range_header else {},
            follow_redirects=True,
        )

    # 5. 透传响应（状态码 + 重要 Headers + 流式 Body）
    return StreamingResponse(
        resp.aiter_bytes(chunk_size=1024 * 512),  # 512KB chunks
        status_code=resp.status_code,
        headers={
            "Content-Type": resp.headers.get("Content-Type", "video/mp4"),
            "Content-Length": resp.headers.get("Content-Length", ""),
            "Content-Range": resp.headers.get("Content-Range", ""),
            "Accept-Ranges": "bytes",
        }
    )
```

### 6.3 Range 请求处理

Plex 在 seek 操作时会发送 `Range: bytes=N-M` 请求。CloudPop 必须：

1. 将 Range 头**原样转发**给上游 115 CDN
2. 返回 `206 Partial Content` 时同样原样透传
3. 不做本地缓冲（流式透传），避免内存爆炸

### 6.4 链接过期处理

```python
# 当上游返回 403/404/410 时，判断为链接过期
if resp.status_code in (403, 404, 410):
    cache.delete(pickcode)
    real_url = await provider.get_download_url(pickcode)  # 重新获取
    cache.set(pickcode, real_url, ttl=3600)
    # 重试一次
```

---

## 7. 缓存层设计

### 7.1 缓存内容

| Key 格式 | Value | TTL |
|----------|-------|-----|
| `dl:{pickcode}` | 真实下载 URL（string） | 3600s（可配置） |
| `fi:{file_id}` | `FileInfo` JSON | 86400s |

### 7.2 存储后端

v0.1 使用**内存缓存**（`cachetools.TTLCache`），无持久化需求：
- 进程重启后缓存清空，下次请求时重新获取
- 内存占用极小（URL 通常 < 500 字节）

未来可扩展为 Redis（多进程/多实例场景）。

### 7.3 接口设计

```python
class CacheManager:
    def get(self, key: str) -> Optional[str]: ...
    def set(self, key: str, value: str, ttl: int) -> None: ...
    def delete(self, key: str) -> None: ...
    def clear(self) -> None: ...
```

---

## 8. Plex 集成设计

### 8.1 Skybox 连接 Plex 的方式

Skybox VR 内置 Plex 客户端，通过 **Plex Media Server API** 浏览媒体库。

当 Skybox 播放某个视频时：
1. Skybox 请求 Plex：`GET http://plex-server:32400/library/metadata/{ratingKey}/…`
2. Plex 读取对应 `.strm` 文件，获取内容 URL：`http://localhost:19798/stream/115/{pickcode}`
3. Skybox 向该 URL 发起流媒体请求（带 Range）
4. CloudPop 代理返回内容

> **关键前提**：Plex 必须先完成对 `.strm` 文件所在目录的**库扫描**，才会在 Skybox 中显示内容。

### 8.2 Plex 库配置建议

| 库名 | 类型 | 根目录 | 说明 |
|------|------|--------|------|
| Movies | 电影 | `{output}/Movies` | 普通电影 |
| TV Shows | 电视节目 | `{output}/TV` | 剧集 |
| VR Videos | 家庭视频/其他 | `{output}/VR` | VR/360° 视频 |

> VR 视频建议单独建"家庭视频"库，因为 Plex 对此类内容不会尝试去 TMDb 刮削元数据。

### 8.3 STRM 文件与 Plex 的兼容性

- Plex 从 **1.20+** 开始原生支持 `.strm` 文件
- STRM 内容必须是**直接可访问的 HTTP/HTTPS URL**
- Plex 不会解析重定向前的 URL 类型，CloudPop 负责返回正确的 `Content-Type`

---

## 9. VR / 360° 视频支持

### 9.1 Skybox 识别规则

Skybox 根据**文件名后缀关键词**判断 VR 类型：

| 文件名特征 | Skybox 识别为 |
|------------|--------------|
| `_180` / `_180x180` | 180° VR |
| `_360` / `_360x360` | 360° 全景 |
| `_SBS` / `_HSBS` | Side-by-Side 3D |
| `_OU` / `_TB` | Over-Under 3D |
| `_LR` | Left-Right 3D |

### 9.2 文件命名要求

CloudPop STRM 生成时**保留原始文件名**（含 VR 标识关键词），使 Skybox 能自动识别类型。

示例：
```
115 网盘: /VR/BigBuckBunny_360_SBS.mp4
STRM:     {output}/VR/BigBuckBunny_360_SBS.strm
内容:     http://localhost:19798/stream/115/abc123pickcode
```

### 9.3 Plex 端的 VR 处理

对于 VR 内容，建议在 Plex 的库中**禁用自动刮削**，使用原始文件名作为标题，避免元数据匹配错误。

---

## 10. 配置系统设计

### 10.1 配置文件路径

默认：`~/.cloudpop/config.yaml`  
可通过环境变量 `CLOUDPOP_CONFIG` 覆盖。

### 10.2 配置文件结构

```yaml
# ~/.cloudpop/config.yaml

server:
  host: "127.0.0.1"
  port: 19798
  # 设置为 true 则监听 0.0.0.0（允许局域网访问）
  public: false

providers:
  "115":
    # 从浏览器 Cookie 中提取
    cookies:
      UID: ""
      CID: ""
      SEID: ""
    # User-Agent 伪装（保持与浏览器一致）
    user_agent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

strm:
  # Plex 监控的本地目录根路径
  output_dir: "~/plex-media"
  # 是否在 STRM 文件内使用 localhost 还是局域网 IP
  # 如果 Plex 和 CloudPop 在同机，使用 localhost
  base_url: "http://localhost:19798"

cache:
  # 下载 URL 缓存时间（秒）
  download_url_ttl: 3600
  # 文件信息缓存时间（秒）
  file_info_ttl: 86400

log:
  level: "INFO"  # DEBUG / INFO / WARNING / ERROR
  file: "~/.cloudpop/cloudpop.log"
```

### 10.3 配置热加载

v0.1 不支持热加载，修改配置后需要重启服务。

---

## 11. API 接口规范

### 11.1 健康检查

```
GET /health

Response 200:
{
  "status": "ok",
  "version": "0.1.0",
  "providers": {
    "115": {
      "authenticated": true
    }
  }
}
```

### 11.2 流媒体端点

```
GET /stream/115/{pickcode}

Headers（可选）:
  Range: bytes=0-1048575

Response 200/206:
  Content-Type: video/x-matroska (或对应类型)
  Accept-Ranges: bytes
  Content-Range: bytes 0-1048575/4294967296  (206 时)
  [Binary video data stream]

Response 404:
  { "detail": "File not found" }

Response 503:
  { "detail": "Provider unavailable" }
```

### 11.3 扫描接口

```
POST /api/scan
Content-Type: application/json

Request:
{
  "provider": "115",
  "path": "/Movies",
  "recursive": true
}

Response 200:
{
  "files": [
    {
      "id": "fid_xxx",
      "pickcode": "abc123",
      "name": "Avatar.mkv",
      "size": 4294967296,
      "path": "/Movies/Avatar (2009)/Avatar.mkv"
    }
  ],
  "total": 1
}
```

### 11.4 STRM 生成接口

```
POST /api/generate
Content-Type: application/json

Request:
{
  "provider": "115",
  "cloud_path": "/Movies",
  "output_path": "~/plex-media/Movies",
  "incremental": false,
  "dry_run": false
}

Response 200:
{
  "created": 125,
  "skipped": 43,
  "errors": 0,
  "duration_seconds": 12.4
}
```

### 11.5 缓存管理接口

```
DELETE /api/cache
Response 200: { "cleared": 256 }

DELETE /api/cache/{pickcode}
Response 200: { "deleted": true }
```

---

## 12. CLI 设计

```
cloudpop [OPTIONS] COMMAND [ARGS]

Options:
  --config PATH    指定配置文件路径
  --verbose        详细日志输出

Commands:
  serve            启动 HTTP 服务
  scan             扫描网盘目录，列出文件
  generate         生成 STRM 文件
  auth check       验证 Cookie 是否有效
  cache clear      清空缓存
  version          显示版本信息
```

### 12.1 典型使用流程

```bash
# 第一步：验证 115 Cookie 配置
cloudpop auth check

# 第二步：扫描目录预览
cloudpop scan --provider 115 --path "/Movies"

# 第三步：生成 STRM 文件
cloudpop generate --provider 115 --path "/Movies" --output "~/plex-media/Movies"

# 第四步：启动代理服务
cloudpop serve

# 第五步（可选）：增量同步
cloudpop generate --provider 115 --path "/Movies" --output "~/plex-media/Movies" --incremental
```

### 12.2 generate 命令详细参数

```
cloudpop generate [OPTIONS]

Options:
  --provider TEXT     网盘提供商（必填，目前只有 115）
  --path TEXT         网盘中的目录路径（必填）
  --output TEXT       本地 STRM 输出目录（必填）
  --incremental       增量模式（只处理新文件）
  --cleanup           删除已不存在于网盘的 STRM 文件
  --dry-run           预演模式，不写入文件，只打印将要执行的操作
  --concurrency INT   并发扫描线程数（默认 3）
```

---

## 13. 目录结构

```
cloudpop/
├── pyproject.toml            # 项目元数据 & 依赖
├── README.md
├── config/
│   └── config.yaml.example   # 配置文件模板
│
├── cloudpop/
│   ├── __init__.py
│   ├── main.py               # FastAPI app 入口 & uvicorn 启动
│   ├── config.py             # 配置加载（pydantic-settings）
│   │
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── base.py           # BaseProvider 抽象类 & FileInfo 数据类
│   │   └── provider_115.py   # 115 网盘实现
│   │
│   ├── strm/
│   │   ├── __init__.py
│   │   ├── generator.py      # STRM 生成逻辑
│   │   └── state.py          # 增量同步状态持久化
│   │
│   ├── proxy/
│   │   ├── __init__.py
│   │   └── stream.py         # 流媒体代理路由
│   │
│   ├── cache/
│   │   ├── __init__.py
│   │   └── manager.py        # TTLCache 封装
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── scan.py           # POST /api/scan
│   │   ├── generate.py       # POST /api/generate
│   │   └── health.py         # GET /health
│   │
│   ├── cli/
│   │   ├── __init__.py
│   │   └── commands.py       # Click CLI 命令
│   │
│   └── models/
│       └── schemas.py        # Pydantic 请求/响应模型
│
└── tests/
    ├── test_provider_115.py
    ├── test_strm_generator.py
    └── test_stream_proxy.py
```

---

## 14. 错误处理规范

| 场景 | 处理方式 | HTTP 状态码 |
|------|----------|------------|
| Cookie 失效 / 未授权 | 返回错误，记录日志，提示重新配置 Cookie | 401 |
| 文件不存在 | 直接返回 | 404 |
| 网盘 API 超时 | 重试 2 次（间隔 1s、2s），超时后返回 | 503 |
| 下载 URL 过期（403） | 静默刷新 URL，重试一次 | 透传或 503 |
| 网盘 API 限流 | 指数退避重试，最多 3 次 | 429 |
| 本地磁盘满（STRM 生成） | 打印错误，跳过该文件，继续处理 | - |

---

## 15. 安全设计

### 15.1 凭据保护

- Cookie 信息存储在本地配置文件，权限设置为 `600`（仅 owner 读写）
- Cookie **永远不**出现在：日志文件、HTTP 响应、API 返回值、STRM 文件内容

### 15.2 网络访问控制

默认配置下 CloudPop 仅监听 `127.0.0.1`，不对外暴露。

Plex 和 CloudPop 同机运行时，Plex 对 STRM 中 `localhost` URL 的访问天然安全。

若需要提供局域网访问（比如 Quest 2 直接访问而不经过 Plex），在 `config.yaml` 中设置 `server.public: true`，并**务必配置防火墙规则**，仅允许局域网 IP 访问。

### 15.3 API 鉴权（可选）

v0.1 不强制 API 鉴权（仅本机使用），但保留 `api_token` 配置项，设置后所有 API 调用需要 `Authorization: Bearer {token}` Header。

---

## 16. 性能目标

| 指标 | 目标值 |
|------|--------|
| 流代理起播延迟 | < 500ms（Cache 命中）；< 1500ms（Cache 未命中） |
| 代理吞吐量 | 支持 4K 流（约 50-100 Mbps）无明显卡顿 |
| 并发流数 | ≥ 5 个并发连接不降速 |
| STRM 生成速度 | ≥ 200 文件/分钟（受 115 API 限速约束） |
| 内存占用 | < 256MB（含缓存） |

### 16.1 性能优化策略

- 使用 `httpx.AsyncClient` 异步 I/O，不阻塞事件循环
- 流式透传，不在内存中缓冲视频数据（`StreamingResponse` + `aiter_bytes`）
- `asyncio.Semaphore` 控制对 115 API 的并发请求数（建议 ≤ 3），防止被封禁

---

## 17. 技术决策记录

### ADR-001：使用 Python + FastAPI（v0.1），预留 Go 迁移路径

**CloudPop 流代理是纯 I/O 转发**（115 CDN → CloudPop → Plex），没有转码或 CPU 密集操作，因此语言性能差距在此场景下不显著。真正的瓶颈是：① 局域网带宽（Wi-Fi 6 轻松承载 150 Mbps 4K VR 内容）；② 115 CDN 下行速度（由账号等级决定）。

| 维度 | Python（FastAPI + httpx） | Go（gin / net/http） |
|------|--------------------------|---------------------|
| 每并发连接内存 | ~2–5 MB | ~50–200 KB（goroutine） |
| 开发速度 | 快 | 较慢 |
| 单二进制部署 | 需要 Python 环境 | 单文件，无依赖 |
| NAS / 低内存设备 | 勉强 | 优秀 |
| 维护生态 | 丰富 | 精简 |

**v0.1 选择 Python 原因**：
- 快速验证完整链路可行性优先于极致性能
- asyncio + httpx `StreamingResponse` 对家用 1–5 路并发完全够用
- 生态丰富，后续扩展（Web UI、定时任务）成熟方案多

**Go 迁移时机（未来考虑）**：
- 需要部署到内存 < 512 MB 的 NAS 设备时
- 并发流路数 > 10 且出现可测量的延迟问题时
- 流代理层代码量很小，届时可以只重写 `proxy/` 模块

### ADR-002：Cookie 鉴权而非 OAuth

**原因**：115 网盘虽然有开放平台（OAuth2.0 AppID），但需要申请审核，且普通用户调用频率受到更严格的限制（SmartStrm 作者实测反馈）。Cookie 方式（UID/CID/SEID/KID）频率限制宽松，且无需申请即可使用，是目前第三方工具的主流选择。

**风险**：Cookie 有一定时效（通常数周到数月），需要用户定期更新。

**v0.2 路线**：集成 QR Code 扫码登录（115driver 已实现 `QRCodeLoginWithApp`），自动获取并管理 Cookie 生命周期，提升用户体验。

### ADR-003：Stream Proxy 而非直接重定向（v0.1）

**背景**：115 下载 URL 有 IP 绑定签名，302 重定向后 Quest2 的 IP 与获取 URL 时的 IP 不同，直接重定向会导致 403 错误。

**可选方案对比**（参考 SmartStrm 的 302/Proxy 双模式经验）：

| 方案 | 优点 | 缺点 |
|------|------|------|
| Proxy（v0.1 选择） | 无 IP 限制；URL 对客户端不透明；统一重试/缓存 | 全量流量经本机，占用带宽 |
| 302 重定向 | 客户端直连 CDN，延迟更低 | 需要 HTTPS + Plex 自定义 server URL；app.plex.tv Web 端不支持 302；仍有 IP 绑定风险 |

**决策**：v0.1 使用 Proxy 模式，本地 Mac 部署场景带宽不是瓶颈，且 Plex 检测到代理服务更可靠。v0.2 可以在 `/stream/115/{pickcode}?mode=302` 提供可选的 302 模式供有 HTTPS 和自定义 URL 的高级用户使用。

### ADR-004：v0.1 使用内存缓存，不引入 Redis

**原因**：本地单机部署，内存缓存完全满足需求，降低部署复杂度。

### ADR-005：下载URL获取 — Chrome扩展API（v0.1） vs App API（v0.2）

**背景**：115 的 App API 使用 m115 ECDH 非对称加密（通过 `go-115` 等库中的 `crypto.GenerateKey` / `Encode` / `Decode` 实现），增加了实现复杂度。Chrome 扩展 API（`proapi.115.com/app/chrome/downurl`）无加密，直接传 pickcode 即可。

**决策**：v0.1 使用 Chrome 扩展 API，降低实现门槛。v0.2 待 Python m115 加密库成熟后（参考 `p115client`）切换至官方 App API，获得更高的稳定性。

---

*本文档描述 CloudPop v0.1 MVP 的设计，后续版本添加新功能时应同步更新此文档。*

---

## 18. 参考项目

以下开源项目在设计阶段被深入研究，关键设计决策均参考其实现经验：

| 项目 | Stars | 语言 | 借鉴内容 |
|------|-------|------|----------|
| [Cp0204/SmartStrm](https://github.com/Cp0204/SmartStrm) | 504 ⭐ | 闭源 | STRM 生成策略、目录修改时间增量检查（§5.5 模式2）、302/Proxy 双模式及 Plex 302 限制文档、字幕文件复制（§5.6）、文件大小过滤（§5.7）、启动延迟 ~10s 属正常现象 |
| [SheltonZhu/115driver](https://github.com/SheltonZhu/115driver) | 144 ⭐ | Go | 下载 URL 加密机制（m115 ECDH，参见 ADR-005）、Search API 字段定义（`SearchOption.Type=4`）、QR Code 登录实现参考 |
| [ChenyangGao/p115client](https://github.com/ChenyangGao/p115client) | 69 ⭐ | Python | Python 接口封装模式、开放平台 OAuth2.0 接入方式参考、m115 加密 Python 实现参考（v0.2） |

---

## 19. 未来扩展方向

以下功能未纳入 v0.1 MVP，但在架构设计中保留了扩展点：

| 功能 | 优先级 | 说明 |
|------|--------|------|
| 多网盘支持（阿里云盘、百度网盘、Onedrive） | 高 | Provider 抽象层已预留接口 |
| QR Code 扫码登录 | 中 | 替代手动填写 Cookie，参考 115driver `QRCodeLoginWithApp` |
| 302 重定向模式 | 中 | `/stream/115/{pickcode}?mode=302`，面向配置了 HTTPS + 自定义 URL 的用户 |
| Web UI（配置 + 扫描状态面板） | 中 | FastAPI 已有路由基础，可叠加 Jinja2 或独立前端 |
| Webhook 触发增量生成 | 低 | 接收 115 云端事件（转存完成等），自动触发 STRM 增量生成，无需定时轮询 |
| App API m115 加密（下载 URL） | 低 | 用 Python 实现 ECDH 加密，替换 Chrome 扩展 API，提升稳定性 |
| 定时任务（Cron） | 低 | 内置 APScheduler 替代外部 crontab |
| Docker 镜像 | 低 | 支持 NAS（群晖/UNRAID）一键部署 |
