"""
FastAPI application entry point.

This module creates and configures the FastAPI application instance.
"""

import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
    ]
)

from __init__ import __version__
from config import settings
from routers import health_router
from routers.notion import router as notion_router
from routers.ai import router as ai_router
from routers.pipeline import router as pipeline_router


def create_app() -> FastAPI:
    """
    Application factory pattern.

    Creates and configures the FastAPI application instance.
    This pattern makes testing easier by allowing multiple app instances.

    Returns:
        FastAPI: Configured application instance
    """
    app = FastAPI(
        title="Memo Synthesizer API",
        description=(
            "Backend API for synthesizing and managing memos through "
            "Notion, Supabase, and AI service integrations."
        ),
        version=__version__,
        docs_url="/docs" if settings.is_development else None,
        redoc_url="/redoc" if settings.is_development else None,
    )

    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(health_router)
    app.include_router(notion_router)
    app.include_router(ai_router)
    app.include_router(pipeline_router)

    return app


# Create application instance
app = create_app()


@app.on_event("startup")
async def startup_event():
    """
    Startup event handler.

    Runs when the application starts.
    Useful for initializing connections, loading resources, etc.
    """
    logger = logging.getLogger(__name__)

    print("=" * 80)
    print(f"Starting Memo Synthesizer API v{__version__}")
    print(f"Environment: {settings.environment}")
    print(f"CORS Origins: {settings.cors_origins_list}")
    print("=" * 80)

    # Validate RPC function availability
    if settings.validate_rpc_on_startup:
        from services.supabase_service import get_supabase_service

        try:
            supabase_service = get_supabase_service()
            await supabase_service.validate_rpc_function_exists()
        except Exception as e:
            logger.warning(f"RPC validation failed during startup: {e}")
            logger.warning("Application will continue using fallback mode")

    print("=" * 80)


@app.on_event("shutdown")
async def shutdown_event():
    """
    Shutdown event handler.

    Runs when the application shuts down.
    Useful for closing connections, cleanup, etc.
    """
    print("Shutting down Memo Synthesizer API")
