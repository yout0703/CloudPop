from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from cloudpop.config import reset_settings
from cloudpop.main import create_app


def _write_config(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "server": {"host": "127.0.0.1", "port": 19798},
                "strm": {
                    "base_url": "http://192.168.110.117:19798",
                    "output_dir": str(path.parent / "media"),
                    "scan_folder_id": "100",
                    "scan_folder_path": "/Movies",
                },
                "dlna": {
                    "enabled": False,
                    "friendly_name": "CloudPop Test DLNA",
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def _write_media(tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    (media_root / "Movies").mkdir(parents=True, exist_ok=True)
    (media_root / "RootVideo.strm").write_text(
        "http://192.168.110.117:19798/stream/115/root/RootVideo.mp4\n",
        encoding="utf-8",
    )
    (media_root / "Movies" / "MovieA.strm").write_text(
        "http://192.168.110.117:19798/stream/115/moviea/MovieA.mp4\n",
        encoding="utf-8",
    )


def test_dashboard_returns_aggregated_status(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    _write_media(tmp_path)

    with TestClient(create_app(config_path=config_path), raise_server_exceptions=False) as client:
        response = client.get("/api/dashboard")

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["scan_folder_path"] == "/Movies"
        assert payload["output_dir"] == str(tmp_path / "media")
        assert payload["dlna"]["friendly_name"] == "CloudPop Test DLNA"
        assert payload["dlna"]["items"] == 2
        assert payload["dlna"]["containers"] == 1
        assert payload["cache"]["size"] == 0
    reset_settings()
