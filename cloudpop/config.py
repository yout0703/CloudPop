"""Configuration loading via pydantic-settings."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_DEFAULT_CONFIG_PATH = Path.home() / ".cloudpop" / "config.yaml"


# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------


class ServerConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    host: str = "127.0.0.1"
    port: int = 19798
    public: bool = False

    @model_validator(mode="after")
    def apply_public(self) -> "ServerConfig":
        if self.public:
            self.host = "0.0.0.0"  # noqa: S104
        return self


class Provider115Cookies(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    UID: str = ""
    CID: str = ""
    SEID: str = ""
    KID: str = ""


class Provider115Config(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    cookies: Provider115Cookies = Provider115Cookies()
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )


class StrmConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    output_dir: str = "~/plex-media"
    base_url: str = "http://localhost:19798"
    min_file_size_mb: int = 0
    copy_subtitles: bool = False
    # 扫描目标文件夹（通过 Web UI 文件夹选择器设置）
    scan_folder_id: str = "0"   # "0" 表示根目录
    scan_folder_path: str = "/"  # 仅用于显示

    @property
    def output_path(self) -> Path:
        return Path(self.output_dir).expanduser().resolve()

    @property
    def is_scan_root(self) -> bool:
        """是否扫描根目录（未设置特定目标文件夹）。"""
        return self.scan_folder_id in ("", "0")


class CacheConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    download_url_ttl: int = 3600
    file_info_ttl: int = 86400


class LogConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    level: str = "INFO"
    file: str = "~/.cloudpop/cloudpop.log"

    @field_validator("level")
    @classmethod
    def upper_level(cls, v: str) -> str:
        return v.upper()


# ---------------------------------------------------------------------------
# Root settings
# ---------------------------------------------------------------------------


class Settings:
    """Root configuration object loaded from YAML."""

    def __init__(self, config_path: Path | None = None) -> None:
        path = config_path or Path(
            os.environ.get("CLOUDPOP_CONFIG", str(_DEFAULT_CONFIG_PATH))
        )
        raw: dict[str, Any] = {}
        if path.exists():
            with open(path) as f:
                raw = yaml.safe_load(f) or {}

        self.server = ServerConfig(**raw.get("server", {}))
        providers_raw = raw.get("providers", {})
        p115_raw = providers_raw.get("115", {})
        cookies_raw = p115_raw.get("cookies", {})
        self.provider_115 = Provider115Config(
            cookies=Provider115Cookies(**cookies_raw),
            **{k: v for k, v in p115_raw.items() if k != "cookies"},
        )
        self.strm = StrmConfig(**raw.get("strm", {}))
        self.cache = CacheConfig(**raw.get("cache", {}))
        self.log = LogConfig(**raw.get("log", {}))

    def is_115_configured(self) -> bool:
        c = self.provider_115.cookies
        return bool(c.UID and c.CID and c.SEID)  # KID 可选


# Singleton – lazily initialized.
_settings: Settings | None = None


def get_settings(config_path: Path | None = None) -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings(config_path)
    return _settings


def reset_settings(config_path: Path | None = None) -> Settings:
    """Force re-load (used in tests)."""
    global _settings
    _settings = Settings(config_path)
    return _settings


def save_scan_folder(
    folder_id: str,
    folder_path: str,
    config_path: Path | None = None,
) -> None:
    """将扫描目标文件夹写入配置文件并刷新内存单例。"""
    path = config_path or Path(
        os.environ.get("CLOUDPOP_CONFIG", str(_DEFAULT_CONFIG_PATH))
    )
    path.parent.mkdir(parents=True, exist_ok=True)

    raw: dict[str, Any] = {}
    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}

    raw.setdefault("strm", {})["scan_folder_id"] = folder_id
    raw.setdefault("strm", {})["scan_folder_path"] = folder_path

    with open(path, "w") as f:
        yaml.safe_dump(raw, f, allow_unicode=True, default_flow_style=False)

    reset_settings(path)


def save_115_cookies(cookies: dict[str, str], config_path: Path | None = None) -> None:
    """将 115 cookies 写入配置文件，并刷新内存中的 Settings 单例。

    只更新 ``providers.115.cookies`` 节点，保留文件中其他现有配置。

    Args:
        cookies: 包含 UID、CID、SEID（和可选 KID）的字典。
        config_path: 配置文件路径，默认为 ~/.cloudpop/config.yaml。
    """
    path = config_path or Path(
        os.environ.get("CLOUDPOP_CONFIG", str(_DEFAULT_CONFIG_PATH))
    )
    path.parent.mkdir(parents=True, exist_ok=True)

    # 读取现有配置（若存在）
    raw: dict[str, Any] = {}
    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}

    # 更新 providers.115.cookies 节点
    raw.setdefault("providers", {}).setdefault("115", {})["cookies"] = {
        "UID": cookies.get("UID", ""),
        "CID": cookies.get("CID", ""),
        "SEID": cookies.get("SEID", ""),
    }
    if cookies.get("KID"):
        raw["providers"]["115"]["cookies"]["KID"] = cookies["KID"]

    # 写回文件
    with open(path, "w") as f:
        yaml.safe_dump(raw, f, allow_unicode=True, default_flow_style=False)

    # 刷新内存单例
    reset_settings(path)
