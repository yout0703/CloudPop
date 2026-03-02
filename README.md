# CloudPop

**Multi-provider cloud media bridge** — convert cloud storage videos to `.strm` files playable by Plex / Skybox VR on Quest 2.

> v0.1 supports **115 网盘**. More providers (Aliyundrive, Baidu, OneDrive) via the `BaseProvider` abstraction.

---

## Why CloudPop?

| Problem | AList mount | CloudPop STRM |
|---------|-------------|---------------|
| Plex scanning triggers massive API calls | ✗ Every file read calls cloud API | ✓ Plex only sees local `.strm` text files |
| Rate-limit / ban risk | High (Plex thumbnail generation) | Minimal (one API call per *playback*) |
| Stability on restart | Mount may disconnect | STRM files persist forever |

---

## Quick Start (5 steps)

### 1. Install

```bash
cd /path/to/CloudPop
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure Cookies

```bash
mkdir -p ~/.cloudpop
cp config/config.yaml.example ~/.cloudpop/config.yaml
```

Open `~/.cloudpop/config.yaml` and fill in your 115 cookies:

```yaml
providers:
  "115":
    cookies:
      UID: "your_uid_here"
      CID: "your_cid_here"
      SEID: "your_seid_here"
```

To get cookies: log into 115.com → DevTools (F12) → Application → Cookies → copy `UID`, `CID`, `SEID`.

Verify:

```bash
cloudpop auth check
# ✓ 认证成功 (115 cookie valid)
```

### 3. Generate STRM Files

```bash
# Preview without writing
cloudpop generate --path "/Movies" --output ~/plex-media/Movies --dry-run

# Actually generate
cloudpop generate --path "/Movies" --output ~/plex-media/Movies
```

### 4. Configure Plex Media Library

In Plex → Settings → Libraries → Add Library:

| Library | Type | Root |
|---------|------|------|
| Movies | Movie | `~/plex-media/Movies` |
| TV Shows | TV | `~/plex-media/TV` |
| VR Videos | Home Videos | `~/plex-media/VR` |

Trigger a library scan. Plex will pick up the `.strm` files immediately.

### 5. Start CloudPop & Play in Skybox

```bash
cloudpop serve
# Starting CloudPop v0.1.0 on http://127.0.0.1:19798
```

Open **Skybox VR** on Quest 2 → Connect to Plex → Browse → Play.

---

## CLI Reference

```
cloudpop serve                     Start HTTP proxy server
cloudpop auth check                Verify 115 credentials
cloudpop scan --path /Movies       List videos in cloud path
cloudpop generate --path /Movies   Generate STRM files
  --output <dir>                   Local output directory
  --incremental                    Only process new files
  --cleanup                        Remove orphan .strm files
  --dry-run                        Preview, don't write
cloudpop cache clear               Clear cached download URLs
cloudpop cache stats               Show cache hit rate
cloudpop version                   Show version
```

---

## VR / 360° File Naming

Skybox auto-detects VR type from filename keywords — **CloudPop preserves original filenames**:

| Keyword | Type |
|---------|------|
| `_360` | 360° equirectangular |
| `_180` | 180° VR |
| `_SBS` / `_HSBS` | Side-by-side 3D |
| `_OU` / `_TB` | Over-under 3D |

---

## Configuration Reference

See `config/config.yaml.example` for all options.

Key settings:

```yaml
strm:
  output_dir: ~/plex-media       # Where .strm files are written
  base_url: http://localhost:19798  # URL written inside .strm files
  min_file_size_mb: 100          # Skip files smaller than this (trailers)
  copy_subtitles: false          # Also copy .srt/.ass to local dir

cache:
  download_url_ttl: 3600         # Cache 115 CDN URLs for 1 hour
```

---

## Architecture

```
Quest 2 (Skybox) → Plex → CloudPop → 115 CDN
```

1. Plex reads `.strm` → URL `http://localhost:19798/stream/115/{pickcode}`
2. CloudPop checks TTL cache for CDN URL
3. On miss: calls `proapi.115.com/app/chrome/downurl`
4. CloudPop proxies the byte range to Plex (transparent Range forwarding)
5. Plex streams to Skybox

---

## Development

```bash
# Run tests
pytest -v

# Lint
ruff check cloudpop tests
```

---

## FAQ

**Q: Startup latency is ~10 seconds?**  
A: Normal — Plex is doing local file stat + metadata lookup. CloudPop itself responds in < 500ms once the connection is established.

**Q: Cookie expired?**  
A: Re-extract from browser and update `~/.cloudpop/config.yaml`. A QR code auto-login is planned for v0.2.

**Q: Can I use 302 redirect instead of proxy?**  
A: 115 CDN URLs are IP-bound, so direct 302 from Plex → Quest2 causes 403. The proxy mode (default) avoids this. v0.2 will offer an optional 302 mode for setups with a custom HTTPS server URL.

---

## License

MIT
