"""
Import job schemas for background task tracking.

These models define the structure for tracking Notion import jobs
that run in the background using FastAPI BackgroundTasks.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID
from pydantic import BaseModel, Field


class ImportJobCreate(BaseModel):
    """Schema for creating a new import job."""

    mode: str = Field(..., pattern="^(database|parent_page)$")
    config_json: Dict[str, Any] = Field(default_factory=dict)

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "mode": "parent_page",
                    "config_json": {"page_size": 100, "timestamp": "2026-01-14T12:00:00Z"},
                }
            ]
        }
    }


class ImportJobUpdate(BaseModel):
    """Schema for updating import job progress."""

    status: Optional[str] = Field(None, pattern="^(pending|processing|completed|failed)$")
    total_pages: Optional[int] = None
    processed_pages: Optional[int] = None
    imported_pages: Optional[int] = None
    skipped_pages: Optional[int] = None
    error_message: Optional[str] = None
    failed_pages: Optional[List[Dict[str, str]]] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "status": "processing",
                    "total_pages": 724,
                    "processed_pages": 123,
                    "imported_pages": 120,
                }
            ]
        }
    }


class FailedPage(BaseModel):
    """Schema for failed page details."""

    page_id: str
    error_message: str

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "page_id": "123e4567-e89b-12d3-a456-426614174000",
                    "error_message": "Rate limit exceeded",
                }
            ]
        }
    }


class ImportJobStatus(BaseModel):
    """Schema for import job status response (public API)."""

    job_id: UUID
    status: str
    mode: str
    total_pages: int
    processed_pages: int
    imported_pages: int
    skipped_pages: int
    progress_percentage: float = Field(..., ge=0, le=100)
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    elapsed_seconds: Optional[float] = None
    error_message: Optional[str] = None
    failed_pages: List[FailedPage] = Field(default_factory=list)
    config_json: Dict[str, Any] = Field(default_factory=dict)

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "job_id": "123e4567-e89b-12d3-a456-426614174000",
                    "status": "processing",
                    "mode": "parent_page",
                    "total_pages": 724,
                    "processed_pages": 123,
                    "imported_pages": 120,
                    "skipped_pages": 0,
                    "progress_percentage": 17.0,
                    "created_at": "2026-01-14T12:00:00Z",
                    "started_at": "2026-01-14T12:00:01Z",
                    "completed_at": None,
                    "elapsed_seconds": 41.3,
                    "error_message": None,
                    "failed_pages": [
                        {
                            "page_id": "abc123",
                            "error_message": "Rate limit exceeded",
                        }
                    ],
                    "config_json": {"page_size": 100},
                }
            ]
        }
    }


class ImportJobStartResponse(BaseModel):
    """Schema for import job start response."""

    job_id: UUID
    status: str = Field(default="pending")
    message: str

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "job_id": "123e4567-e89b-12d3-a456-426614174000",
                    "status": "pending",
                    "message": "Import job started (mode: parent_page). Use GET /pipeline/import-status/{job_id} to check progress.",
                }
            ]
        }
    }
