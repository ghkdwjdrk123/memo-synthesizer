"""
Health check schemas.
"""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Health check response schema."""

    status: str = Field(
        ...,
        description="Health status",
        example="ok"
    )
    version: str = Field(
        default="0.1.0",
        description="API version"
    )
    environment: str = Field(
        ...,
        description="Current environment"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "ok",
                "version": "0.1.0",
                "environment": "development"
            }
        }
    }
