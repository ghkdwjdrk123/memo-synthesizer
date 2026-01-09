"""
Health check endpoints.
"""

from fastapi import APIRouter, Depends

from config import Settings, get_settings
from schemas.health import HealthResponse
from __init__ import __version__

router = APIRouter(prefix="", tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health Check",
    description="Check if the API is running and return basic system information."
)
async def health_check(
    settings: Settings = Depends(get_settings)
) -> HealthResponse:
    """
    Health check endpoint.

    Returns:
        HealthResponse: API health status and metadata
    """
    return HealthResponse(
        status="ok",
        version=__version__,
        environment=settings.environment
    )
