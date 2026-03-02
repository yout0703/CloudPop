# CloudPop — 开发任务计划 v0.1

> 目标：实现 MVP，完成 115 网盘 → STRM → Plex → Quest 2/Skybox 完整链路  
> 预估周期：4 个迭代（Sprint），每个 Sprint 约 1 周

---

## 整体里程碑

```
Sprint 1          Sprint 2          Sprint 3          Sprint 4
──────────        ──────────        ──────────        ──────────
项目脚手架         115 Provider       流媒体代理         集成 & 打磨
配置系统           STRM 生成          CLI 完善           测试 & 文档
基础抽象层         缓存层             Plex 验证          发布 v0.1
```

---

## Sprint 1：项目脚手架与基础层

**目标**：搭建可运行的项目骨架，跑通最简单的 `/health` 接口

### 任务清单

#### S1-T1 项目初始化

- [ ] 创建 `pyproject.toml`，配置依赖项
  - 核心：`fastapi`, `uvicorn[standard]`, `httpx`, `click`, `pydantic-settings`, `pyyaml`, `cachetools`
  - 开发：`pytest`, `pytest-asyncio`, `httpx` (testclient), `ruff`
- [ ] 初始化 git 仓库，创建 `.gitignore`（忽略 `*.pyc`, `.env`, `config.yaml`）
- [ ] 创建完整目录结构（参考 design.md §13）
- [ ] 配置 `ruff` 代码规范检查

**验收标准**：`pip install -e .` 成功，`pytest` 可以运行

---

#### S1-T2 配置系统

- [ ] 实现 `cloudpop/config.py`
  - 使用 `pydantic-settings` 加载 `config.yaml`
  - 支持环境变量 `CLOUDPOP_CONFIG` 覆盖配置文件路径
  - 配置项：`ServerConfig`, `ProviderConfig`, `StrmConfig`, `CacheConfig`, `LogConfig`
- [ ] 创建 `config/config.yaml.example` 配置模板
- [ ] 配置文件不存在时给出友好提示

**验收标准**：`from cloudpop.config import settings` 能正确加载配置

---

#### S1-T3 FastAPI 应用骨架

- [ ] 实现 `cloudpop/main.py`
  - 创建 FastAPI app 实例
  - 挂载路由（`/health`, `/stream`, `/api`）
  - 配置日志（structlog 或标准 logging）
  - 启动时验证配置完整性
- [ ] 实现 `GET /health` 端点

**验收标准**：`cloudpop serve` 启动后，`curl http://localhost:19798/health` 返回 `{"status":"ok"}`

---

#### S1-T4 Provider 抽象层

- [ ] 实现 `cloudpop/providers/base.py`
  - `FileInfo` 数据类
  - `BaseProvider` 抽象基类
  - 自定义异常：`AuthError`, `ProviderError`, `FileNotFoundError`, `RateLimitError`
- [ ] 实现 `Provider` 工厂函数：`get_provider(name: str) -> BaseProvider`

**验收标准**：`BaseProvider` 接口定义清晰，子类实现必须通过 `mypy` 类型检查

---

#### S1-T5 缓存层

- [ ] 实现 `cloudpop/cache/manager.py`
  - 基于 `cachetools.TTLCache` 的 `CacheManager`
  - 方法：`get`, `set`, `delete`, `clear`, `stats`（返回缓存条目数和命中率）
- [ ] 单例模式，从 `main.py` 初始化并注入依赖

**验收标准**：单元测试覆盖 TTL 过期、命中、未命中三种场景

---

## Sprint 2：核心功能 — 115 Provider & STRM 生成

**目标**：能够扫描 115 网盘并在本地生成 STRM 文件

### 准备工作

> 开始本 Sprint 前需确认：
> - 拥有有效的 115 账号 Cookie（UID / CID / SEID）
> - 测试目录在 115 网盘中已存在几个视频文件

---

#### S2-T1 115 Provider —— 认证

- [ ] 实现 `cloudpop/providers/provider_115.py`：`Provider115` 类
- [ ] `authenticate()` 方法
  - 调用 `https://passportapi.115.com/app/1.0/web/1.0/check/sso` 验证 Cookie 有效性
  - 返回 `True` / 抛出 `AuthError`
- [ ] 请求中统一附加正确的 `User-Agent`、`Referer` 头
- [ ] `cloudpop auth check` CLI 命令调用此方法并输出结果

**验收标准**：使用真实 Cookie 配置运行 `cloudpop auth check` 输出 `✓ 认证成功`

---

#### S2-T2 115 Provider —— 文件搜索与路径解析

> 采用两阶段方案（按类型搜索 + 批量路径解析），比逐目录递归减少约 70% API 调用，降低风控风险。

- [ ] 实现 `search_videos(folder_id: str)` 方法（**主要搜索方法**）
  - 调用 `https://webapi.115.com/files/search?cid={id}&type=4&limit=100` API
  - `type=4` 直接筛选视频文件，无需客户端过滤扩展名
  - 自动翻页，返回 `Iterator[FileInfo]`，包含 `(pickcode, cid, name, modified_at)`
- [ ] 实现 `get_folder_path(cid: str)` 方法
  - 查询单个文件夹的完整路径数组（根目录 → 当前文件夹）
  - 返回如 `["Movies", "Avatar (2009)"]` 的列表
- [ ] 实现 `batch_resolve_paths(cids: set[str])` 方法
  - 对所有唯一 cid 并发查询路径（`asyncio.gather`，并发数 ≤ 5）
  - 返回 `{cid: "/Movies/Avatar (2009)"}` 映射表
- [ ] 实现 `find_folder_id(path: str)` 方法（备用）
  - 调用 `/files/getid?path=...` 直接解析路径为 folder_id（单次请求）

**验收标准**：
- `cloudpop scan --provider 115 --path "/Movies"` 能一次性列出所有层级的视频文件
- 对比逐目录递归，500 个视频的扫描 API 调用次数 < 60 次

---

#### S2-T3 115 Provider —— 下载 URL

- [ ] 实现 `get_download_url(pickcode: str)` 方法
  - 调用 `https://proapi.115.com/app/chrome/downurl?pickcode={pickcode}`
  - 解析响应，返回真实下载 URL
  - 下载 URL 不可用时抛出 `ProviderError`
- [ ] 实现 `get_file_info(file_id: str)` 方法（可选，v0.1 可以简化）

**验收标准**：使用真实 pickcode 调用该方法，返回的 URL 用 `curl -L` 能下载到文件

---

#### S2-T4 STRM 生成器

- [ ] 实现 `cloudpop/strm/generator.py`：`StrmGenerator` 类
  - `generate(provider, cloud_path, output_dir, incremental, dry_run)` 主方法
  - **阶段一**：调用 `provider.search_videos(folder_id)` 拉取全量视频文件列表
  - **阶段二**：收集所有唯一 `cid`，调用 `provider.batch_resolve_paths()` 批量解析路径
  - **阶段三**：根据路径映射表生成 STRM 文件，保留完整目录层级
  - STRM 文件内容：`{base_url}/stream/115/{pickcode}`
  - 生成统计：created / skipped / errors
- [ ] 实现 `cloudpop/strm/state.py`
  - 读写 `state.json`（持久化 `cid_path_map` + 上次扫描时间戳 per cloud_path）
  - 增量模式：搜索时加 `?asc=0&o=user_utime` 并截止到上次扫描时间，路径 map 复用缓存，只补充查询新出现的 `cid`

**验收标准**：
- 运行 `cloudpop generate --dry-run` 打印将要创建的文件列表，不实际写入
- 运行 `cloudpop generate` 在本地目录生成正确的 STRM 文件，目录层级与网盘一致
- 二次增量运行时 API 调用次数显著少于首次

---

#### S2-T5 API 端点 —— 扫描 & 生成

- [ ] 实现 `POST /api/scan`（调用 `list_files` 并返回文件列表 JSON）
- [ ] 实现 `POST /api/generate`（异步执行 STRM 生成，返回统计结果）
- [ ] 实现 `DELETE /api/cache` 和 `DELETE /api/cache/{pickcode}`

**验收标准**：使用 `curl` 测试上述接口返回预期结果

---

## Sprint 3：流媒体代理 & 端到端集成

**目标**：Plex 能够通过 CloudPop 代理流畅播放 115 网盘视频

---

#### S3-T1 流媒体代理端点

- [ ] 实现 `cloudpop/proxy/stream.py`：`GET /stream/115/{pickcode}`
  - Cache 命中分支：直接取 URL 转发
  - Cache 未命中分支：调用 `get_download_url()`，写入 Cache，再转发
  - 使用 `httpx.AsyncClient` 发起上游请求
  - 正确转发 `Range` 请求头
  - 返回 `StreamingResponse`，透传 `Content-Type`、`Content-Range`、`Content-Length`、`Accept-Ranges`
- [ ] 链接过期处理（上游返回 403/410 时刷新 URL 并重试一次）
- [ ] 添加简单的访问日志（pickcode、客户端 IP、Range、状态码、响应时间）

**验收标准**：
```bash
# 能够播放视频
curl -H "Range: bytes=0-1048575" \
     http://localhost:19798/stream/115/{pickcode} \
     -o test.bin
# test.bin 大小为 1048576 字节，且为有效视频数据
```

---

#### S3-T2 Plex 集成验证

- [ ] 在本地 Plex Media Server 配置媒体库，根目录指向 STRM 输出目录
- [ ] 触发 Plex 库扫描，观察 STRM 是否被正确识别
- [ ] 在 Plex Web 界面尝试播放一个 STRM 对应的视频
- [ ] 记录发现的问题和调整内容

**验收标准**：Plex Web 界面能流畅播放通过 CloudPop 代理的 115 视频

---

#### S3-T3 Quest 2 / Skybox 集成验证

- [ ] 在 Skybox 中添加 Plex 服务器（需要 Plex 账号登录或局域网发现）
- [ ] 浏览媒体库，找到 CloudPop 生成的 STRM 文件对应条目
- [ ] 播放普通 2D 视频，验证流畅度
- [ ] 播放带 VR 标识的视频（`_360` / `_SBS`），验证 Skybox 能正确识别类型
- [ ] 记录问题

**验收标准**：Skybox 能通过 Plex 播放 115 网盘视频，VR 视频类型自动识别正确

---

#### S3-T4 CLI 完善

- [ ] 实现完整 CLI 入口（`cloudpop/cli/commands.py`，基于 `click`）
  - `cloudpop serve`：启动服务
  - `cloudpop scan`：扫描目录
  - `cloudpop generate`：生成 STRM
  - `cloudpop auth check`：验证认证
  - `cloudpop cache clear`：清空缓存
  - `cloudpop version`：版本信息
- [ ] 注册为 `pyproject.toml` scripts 入口点

**验收标准**：`cloudpop --help` 显示所有命令帮助信息

---

## Sprint 4：测试、文档与发布

**目标**：完善测试，整理文档，打包 v0.1

---

#### S4-T1 单元测试

- [ ] `test_provider_115.py`：使用 `respx`（httpx mock）mock 115 API
  - 测试 `list_files` 分页
  - 测试 `get_download_url` 正常 / 失败场景
  - 测试 `authenticate` 成功 / Cookie 失效
- [ ] `test_strm_generator.py`：
  - 测试目录递归
  - 测试 STRM 文件内容正确性
  - 测试增量模式（仅处理新文件）
  - 测试 dry-run 不写入文件
- [ ] `test_stream_proxy.py`：
  - 测试 Cache 命中路径
  - 测试 Cache 未命中路径
  - 测试 Range 请求正确转发
  - 测试链接过期自动刷新

**验收标准**：`pytest` 通过率 > 80%，关键路径 100% 覆盖

---

#### S4-T2 错误处理与健壮性

- [ ] 全局异常处理中间件（FastAPI `exception_handler`）
- [ ] 115 API 限流处理（指数退避重试）
- [ ] STRM 生成时磁盘空间检查
- [ ] Cookie 失效时输出明确的错误信息和操作指引

---

#### S4-T3 README 文档

- [ ] 编写 `README.md`
  - 功能简介
  - 快速开始（5 步之内跑通）：
    1. 安装
    2. 配置 Cookie
    3. 生成 STRM
    4. 配置 Plex 媒体库
    5. 启动服务 & Skybox 播放
  - 配置项说明
  - 常见问题 FAQ

---

#### S4-T4 打包与发布

- [ ] 确认 `pyproject.toml` 版本为 `0.1.0`
- [ ] 验证 `pip install .` 在干净环境中可正常使用
- [ ] （可选）打包为 macOS `.app` 或提供 `brew install` 支持
- [ ] 打 git tag `v0.1.0`

---

## 已知风险与应对措施

| 风险 | 概率 | 影响 | 应对措施 |
|------|------|------|----------|
| 115 下载 URL IP 绑定，Quest 2 无法直接使用 | 高 | 高 | **本设计通过 CloudPop 代理解决**：Quest 2 → Plex → CloudPop（同 IP）→ 115，URL 只由 CloudPop 调用 |
| 115 API 接口变更 | 中 | 高 | 参考 AList/elevengo 等开源项目保持同步，关键接口写集成测试 |
| Plex 不识别 STRM 内容类型 | 低 | 中 | 确保 CloudPop 返回正确的 `Content-Type` 头；必要时添加 `/stream/115/{pickcode}/metadata` 端点 |
| Cookie 频繁失效 | 中 | 中 | v0.2 引入 QR Code 登录自动刷新；v0.1 提供详细的 Cookie 更新操作文档 |
| 115 API 限速 | 中 | 低 | STRM 生成时控制并发（`asyncio.Semaphore(3)`），生产环境加指数退避重试 |

---

## 依赖关系图

```
S1-T1 (脚手架)
  └── S1-T2 (配置)
        └── S1-T3 (FastAPI 骨架) ──────────────────────────┐
              └── S1-T4 (Provider 抽象)                    │
                    └── S2-T1 (115 认证)                   │
                          └── S2-T2 (文件列表)             │
                                ├── S2-T3 (下载 URL)       │
                                │     └── S3-T1 (流代理) ◄─┘
                                │           └── S3-T2 (Plex 验证)
                                │                 └── S3-T3 (Skybox 验证)
                                └── S2-T4 (STRM 生成)
                                      └── S2-T5 (API 端点)
S1-T5 (缓存层) ─────────────────────────────────► S3-T1
```

---

## 后续版本规划（v0.2+）

| 功能 | 版本 | 说明 |
|------|------|------|
| QR Code 自动登录 & Token 刷新 | v0.2 | 解决 Cookie 手动更新痛点 |
| 定时自动同步（cron 模式） | v0.2 | 新增文件自动生成 STRM |
| Web UI 管理界面 | v0.3 | 浏览文件、手动触发同步、查看缓存状态 |
| Plex 自动触发库刷新 | v0.2 | STRM 生成完成后调用 Plex API 触发扫描 |
| 多网盘支持（Aliyun / WebDAV） | v0.3 | 复用抽象层，扩展 Provider |
| Docker 镜像 | v0.2 | 方便 NAS 部署（群晖/威联通） |

---

*最后更新：2026-03-01*
