# CloudPop — Technical Design & Implementation Specification

## 1. Overview

CloudPop is a multi-provider cloud media bridge that converts cloud storage video files into STRM references and exposes streaming endpoints for media servers such as Plex, Emby, Jellyfin, and Infuse.

CloudPop enables users to stream cloud-hosted video files without downloading them locally.

Core principle:

Cloud Storage → CloudPop → STRM / Proxy → Plex → Playback Device

---

## 2. Goals

### Primary goals

- Generate STRM files from cloud storage
- Provide stable streaming endpoints
- Support multiple cloud providers
- Integrate seamlessly with Plex / Emby / Jellyfin
- Avoid direct link expiration issues
- Enable automatic library sync

### Secondary goals

- Web UI management
- Multi-user support
- Plugin-based provider architecture

---

## 3. Non-Goals (v0.1)

- No transcoding
- No DRM bypass
- No torrent integration
- No metadata scraping (Plex handles this)

---

## 4. Core Concepts

### 4.1 STRM File

STRM file contains a URL pointing to CloudPop proxy endpoint.

Example file:

Avatar (2009).strm

Contents:

http://localhost:19798/stream/115/123456

---

### 4.2 Streaming Proxy

CloudPop provides streaming endpoint:

GET /stream/{provider}/{file_id}

CloudPop resolves real download URL and streams content.

---

### 4.3 Provider

Provider represents cloud storage backend.

Examples:

- 115 Drive
- Aliyun Drive
- Google Drive
- WebDAV
- Dropbox
- OneDrive
- S3

---

## 5. System Architecture

            ┌────────────────────┐
            │   Cloud Storage     │
            │ (115, GDrive, etc) │
            └─────────┬──────────┘
                      │ API
                      ▼
            ┌────────────────────┐
            │    CloudPop Core    │
            │                    │
            │ Provider Manager  │
            │ Stream Proxy      │
            │ STRM Generator    │
            │ Cache Manager     │
            └───────┬──────────┘
                    │ HTTP
                    ▼
            ┌────────────────────┐
            │      Plex          │
            └─────────┬──────────┘
                      │
                      ▼
                 Playback Device

---

## 6. Component Design

### 6.1 cloudpop-server

Main HTTP server

Responsibilities:

- Stream proxy
- API server
- Provider coordination

---

### 6.2 cloudpop-provider

Provider abstraction layer

Interface:

```python
class Provider:

    def list_files(self, path: str) -> list:
        pass

    def get_download_url(self, file_id: str) -> str:
        pass

    def get_file_info(self, file_id: str):
        pass


⸻

6.3 cloudpop-strm

STRM generation module

Responsibilities:
	•	Scan cloud folders
	•	Generate STRM files
	•	Maintain folder structure

⸻

6.4 cloudpop-cache

Handles caching:
	•	download URLs
	•	file metadata

⸻

7. Provider Interface Specification

Provider must implement:

class File:
    id: str
    name: str
    size: int
    is_dir: bool
    path: str


class Provider:

    def authenticate(self, config):
        pass

    def list_files(self, path):
        pass

    def get_download_url(self, file_id):
        pass

    def refresh_token(self):
        pass


⸻

8. Streaming Proxy Specification

Endpoint:

GET /stream/{provider}/{file_id}

Flow:

Client → CloudPop → Provider → Real URL → Stream to Client

Must support HTTP Range header.

Example:

Range: bytes=0-1048576

CloudPop must forward Range request.

⸻

9. STRM Generation Specification

Input:

Provider path:
/Movies/Avatar (2009)/Avatar.mkv

Output:

/Plex/Movies/Avatar (2009)/Avatar.strm

STRM content:

http://localhost:19798/stream/115/123456


⸻

10. API Specification

Scan folder

POST /api/scan

Request:

{
  "provider": "115",
  "path": "/Movies"
}


⸻

Generate STRM

POST /api/generate

Request:

{
  "provider": "115",
  "path": "/Movies",
  "output": "/mnt/plex/Movies"
}


⸻

Stream endpoint

GET /stream/{provider}/{file_id}


⸻

Health check

GET /health

Response:

OK


⸻

11. Folder Structure

Recommended project layout:

cloudpop/

  server/
    main.py
    routes.py

  providers/
    base.py
    provider_115.py
    provider_webdav.py

  strm/
    generator.py

  cache/
    manager.py

  models/
    file.py

  config/
    config.yaml


⸻

12. Configuration Specification

config.yaml

server:
  port: 19798

providers:

  115:
    cookie: ""

  webdav:
    url: ""
    username: ""
    password: ""

strm:
  output_dir: "/mnt/plex"

cache:
  ttl: 3600


⸻

13. Streaming Flow

Plex
 ↓
CloudPop /stream/115/123456
 ↓
Provider.get_download_url()
 ↓
Provider returns real URL
 ↓
CloudPop forwards stream
 ↓
Plex plays video


⸻

14. Cache Strategy

Cache:
	•	download URL
	•	file info

TTL recommended:

3600 seconds

Purpose:
	•	Reduce provider API calls
	•	Improve performance

⸻

15. Error Handling

CloudPop must handle:
	•	expired link
	•	invalid token
	•	provider timeout
	•	file not found

Return appropriate HTTP status codes:
	•	404
	•	401
	•	500
	•	503

⸻

16. CLI Specification

Commands:

Scan:

cloudpop scan

Generate STRM:

cloudpop generate

Run server:

cloudpop serve


⸻

17. Performance Requirements

Must support:
	•	4K streaming
	•	Range requests
	•	concurrent streams (10+)

Latency target:

< 200ms proxy overhead


⸻

18. Security Considerations

Must NOT expose:
	•	provider credentials
	•	cookies

Optional:
	•	token authentication
	•	local network restriction

⸻

19. Future Extensions

Future features:
	•	Web UI
	•	Multi-user
	•	Metadata caching
	•	Plex auto-refresh
	•	Docker support
	•	Kubernetes deployment

⸻

20. Minimal Viable Product (v0.1)

Must implement:
	•	Provider interface
	•	115 provider
	•	Streaming proxy
	•	STRM generation
	•	CLI
	•	Config file

⸻

21. Technology Stack Recommendation

Recommended (Python):
	•	FastAPI
	•	httpx
	•	uvicorn

Alternative (Go):
	•	gin
	•	net/http

⸻

22. Example Full Flow

User runs:

cloudpop generate

CloudPop generates:

Avatar.strm

User adds folder to Plex.

Plex plays video via CloudPop proxy.

⸻

23. Expected Result

CloudPop enables:
	•	instant playback
	•	no local download
	•	seamless Plex integration
	•	multi-provider streaming support

⸻

End of Specification
