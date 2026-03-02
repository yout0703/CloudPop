# CloudPop — AI Agent 工作指南

## 语言要求

**所有对话、回复、注释建议、代码说明均必须使用中文。**
包括但不限于：解释、提问、错误分析、方案建议、commit message 草稿等。

---

## 项目简介

CloudPop 是一个多网盘媒体桥接工具，核心功能：

- 将 115 网盘中的视频文件转换为本地 `.strm` 文件，供 Plex Media Server 索引
- 提供流媒体代理服务，将 Plex 的 Range 请求转发至 115 CDN
- 支持增量同步，最小化对 115 API 的调用频率，降低风控风险

目标用户：在 Quest 2 上通过 Skybox VR Player + Plex 播放 115 网盘 4K/VR 内容。

---

## 技术栈

| 层次 | 技术 |
|------|------|
| 语言 | Python 3.12+ |
| Web 框架 | FastAPI + uvicorn |
| HTTP 客户端 | httpx（异步） |
| CLI | Click |
| 配置 | pydantic-settings + YAML |
| 缓存 | cachetools TTLCache（内存） |
| 包管理 | **uv**（`uv sync` 安装依赖，`uv run` 执行命令） |
| 测试 | pytest + pytest-asyncio + respx |

---

## 目录结构

```
cloudpop/
├── cloudpop/
│   ├── main.py               # FastAPI app 工厂（lifespan 方式启动）
│   ├── config.py             # 配置加载，Settings / get_settings()
│   ├── providers/
│   │   ├── base.py           # BaseProvider 抽象类、FileInfo、异常类
│   │   └── provider_115.py   # 115 网盘实现
│   ├── strm/
│   │   ├── generator.py      # STRM 三阶段生成逻辑
│   │   └── state.py          # 增量同步状态持久化（~/.cloudpop/state.json）
│   ├── proxy/
│   │   └── stream.py         # GET /stream/115/{pickcode} 流媒体代理
│   ├── cache/
│   │   └── manager.py        # CacheManager，TTLCache 封装
│   ├── api/
│   │   ├── health.py         # GET /health
│   │   ├── scan.py           # POST /api/scan
│   │   ├── generate.py       # POST /api/generate
│   │   └── cache.py          # DELETE /api/cache[/{pickcode}]
│   ├── cli/
│   │   └── commands.py       # Click CLI 入口
│   └── models/
│       └── schemas.py        # Pydantic 请求/响应模型
└── tests/
    ├── test_provider_115.py
    ├── test_strm_generator.py
    └── test_stream_proxy.py
```

---

## 常用命令

```bash
# 安装依赖（含开发工具）
uv sync

# 运行全部测试
uv run pytest -v

# 运行单个测试文件
uv run pytest tests/test_provider_115.py -v

# 代码格式检查
uv run ruff check cloudpop tests

# 启动服务（需先配置 ~/.cloudpop/config.yaml）
uv run cloudpop serve
```

---

## 开发约定

### 代码风格
- 行长限制 100 字符（见 `pyproject.toml` ruff 配置）
- 类型注解：所有公开函数必须有完整的类型注解
- 异步优先：I/O 操作统一使用 `async/await`，禁止在异步上下文中使用同步阻塞调用
- 导入排序由 ruff 管理（`I` 规则集）

### 115 API 注意事项
- Cookie 字段：`UID`、`CID`、`SEID`（必填），`KID`（可选）
- User-Agent 必须伪装为浏览器，否则返回 403
- 下载 URL 有效期约 3600 秒，且绑定请求者 IP
- 分页时用 `offset += len(items)`，**不能**用 `offset += limit`（数据量小于 limit 时会提前终止）
- API 调用间隔 ≥ 0.3 秒，防止触发 115 风控

### 缓存使用
- 下载 URL 缓存 key 格式：`dl:{pickcode}`
- 通过 `get_cache()` 获取单例，测试中用 `reset_cache()` 清理状态

### 测试规范
- 网络请求一律使用 `respx.mock` 拦截，**禁止**在测试中发起真实网络请求
- 文件系统操作使用 `tmp_path` fixture（pytest 内置）
- Provider 注入：`Provider115(cookies=..., user_agent=..., client=mock_client)`
- 每个测试函数独立，不依赖其他测试的副作用

---

## 设计文档

详细设计见 [design.md](design.md)，包含：
- 系统架构图与模块职责
- 115 API 端点说明（含真实性说明）
- STRM 生成两阶段方案
- 流媒体代理与 Range 请求处理
- 缓存策略与技术决策记录（ADR-001 ～ ADR-005）

---

## 已知局限（v0.1）

- 仅支持 115 网盘，其他网盘接入需实现 `BaseProvider`
- 下载 URL 使用 Chrome 扩展 API（无加密），v0.2 计划切换至 m115 ECDH 加密的 App API
- 配置修改后需重启服务（不支持热加载）
- Cookie 失效后需用户手动更新配置文件
