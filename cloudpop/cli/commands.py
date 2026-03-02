"""Click CLI entry point for CloudPop."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import click

from cloudpop import __version__


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


@click.group()
@click.option("--config", "config_path", default=None, help="Path to config.yaml")
@click.option("--verbose", is_flag=True, default=False, help="Enable debug logging")
@click.pass_context
def cli(ctx: click.Context, config_path: str | None, verbose: bool) -> None:
    """CloudPop – cloud media bridge for Plex / Skybox VR."""
    ctx.ensure_object(dict)

    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if config_path:
        from cloudpop.config import reset_settings
        reset_settings(Path(config_path))

    ctx.obj["verbose"] = verbose


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--host", default=None, help="Override server host")
@click.option("--port", default=None, type=int, help="Override server port")
def serve(host: str | None, port: int | None) -> None:
    """Start the CloudPop HTTP proxy server."""
    import uvicorn
    from cloudpop.config import get_settings
    from cloudpop.main import create_app

    settings = get_settings()
    app = create_app()
    h = host or settings.server.host
    p = port or settings.server.port
    click.echo(f"Starting CloudPop v{__version__} on http://{h}:{p}")
    uvicorn.run(app, host=h, port=p, log_level="info")


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


@cli.group()
def auth() -> None:
    """Authentication management."""


@auth.command("check")
def auth_check() -> None:
    """Verify that the 115 Cookie credentials are valid."""

    async def _check() -> None:
        from cloudpop.providers.base import AuthError
        from cloudpop.providers.provider_115 import get_provider

        provider = get_provider("115")
        try:
            ok = await provider.authenticate()
            if ok:
                click.echo("✓ 认证成功 (115 cookie valid)")
            else:
                click.echo("✗ 认证失败", err=True)
        except AuthError as exc:
            click.echo(f"✗ 认证失败: {exc}", err=True)

    asyncio.run(_check())


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--provider", default="115", show_default=True, help="Cloud provider")
@click.option("--path", default="/", show_default=True, help="Cloud directory path")
def scan(provider: str, path: str) -> None:
    """Scan a cloud directory and list video files."""

    async def _scan() -> None:
        from cloudpop.providers.base import AuthError, ProviderError
        from cloudpop.providers.provider_115 import get_provider as _gp

        p = _gp(provider)
        folder_id = await p.find_folder_id(path)
        if folder_id is None:
            click.echo(f"✗ Path not found: {path!r}", err=True)
            return

        count = 0
        try:
            async for fi in p.search_videos(folder_id):
                click.echo(f"  [{fi.size:>14,}B]  {path.rstrip('/')}/{fi.name}")
                count += 1
        except (AuthError, ProviderError) as exc:
            click.echo(f"✗ Error: {exc}", err=True)
            return

        click.echo(f"\nTotal: {count} video files")

    asyncio.run(_scan())


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--provider", default="115", show_default=True)
@click.option("--path", "cloud_path", default="/", show_default=True, help="Cloud directory path")
@click.option(
    "--output", "output_dir", default="", help="Local STRM output directory (default: config)"
)
@click.option("--incremental", is_flag=True, default=False, help="Only process new/changed files")
@click.option("--cleanup", is_flag=True, default=False, help="Remove orphan STRM files")
@click.option("--dry-run", is_flag=True, default=False, help="Preview without writing files")
def generate(
    provider: str,
    cloud_path: str,
    output_dir: str,
    incremental: bool,
    cleanup: bool,
    dry_run: bool,
) -> None:
    """Generate STRM files from a cloud directory."""

    async def _generate() -> None:
        from cloudpop.config import get_settings
        from cloudpop.providers.provider_115 import get_provider as _gp
        from cloudpop.strm.generator import StrmGenerator

        settings = get_settings()
        p = _gp(provider)
        out = Path(output_dir).expanduser().resolve() if output_dir else settings.strm.output_path

        if dry_run:
            click.echo(f"[DRY RUN] Would generate STRM files from {cloud_path!r} → {out}")

        gen = StrmGenerator(
            provider=p,
            base_url=settings.strm.base_url,
            output_dir=out,
            min_file_size_mb=settings.strm.min_file_size_mb,
        )
        result = await gen.generate(
            cloud_path=cloud_path,
            incremental=incremental,
            dry_run=dry_run,
            cleanup=cleanup,
        )

        click.echo(
            f"\nDone in {result.duration_seconds:.1f}s: "
            f"created={result.created} skipped={result.skipped} errors={result.errors}"
        )
        if result.error_details:
            click.echo("Errors:")
            for e in result.error_details:
                click.echo(f"  - {e}", err=True)

    asyncio.run(_generate())


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------


@cli.group()
def cache() -> None:
    """Cache management."""


@cache.command("clear")
def cache_clear() -> None:
    """Clear all cached download URLs."""
    from cloudpop.cache.manager import get_cache

    n = get_cache().clear()
    click.echo(f"Cleared {n} cache entries")


@cache.command("stats")
def cache_stats() -> None:
    """Show cache statistics."""
    from cloudpop.cache.manager import get_cache

    stats = get_cache().stats()
    click.echo(
        f"size={stats['size']} hits={stats['hits']} "
        f"misses={stats['misses']} hit_rate={stats['hit_rate']:.1%}"
    )


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


@cli.command()
def version() -> None:
    """Show CloudPop version."""
    click.echo(f"CloudPop {__version__}")
