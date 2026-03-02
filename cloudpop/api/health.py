"""GET /health endpoint."""

from __future__ import annotations

from cloudpop import __version__
from cloudpop.config import get_settings
from cloudpop.models.schemas import HealthResponse, ProviderStatus
from fastapi import APIRouter

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    providers: dict[str, ProviderStatus] = {}

    if settings.is_115_configured():
        from cloudpop.providers.provider_115 import get_provider
        from cloudpop.providers.base import AuthError
        provider = get_provider("115")
        try:
            await provider.authenticate()
            providers["115"] = ProviderStatus(authenticated=True)
        except AuthError as exc:
            providers["115"] = ProviderStatus(authenticated=False, error=str(exc))
        except Exception as exc:
            providers["115"] = ProviderStatus(authenticated=False, error=str(exc))
    else:
        providers["115"] = ProviderStatus(authenticated=False, error="No credentials configured")

    return HealthResponse(
        status="ok",
        version=__version__,
        providers=providers,
    )
