"""
Notion-related schemas.
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class NotionDatabaseInfo(BaseModel):
    """Notion database information schema."""

    success: bool = Field(..., description="Whether the request was successful")
    database_id: Optional[str] = Field(None, description="Database ID")
    title: Optional[str] = Field(None, description="Database title")
    properties: Optional[List[str]] = Field(None, description="List of property names")
    created_time: Optional[str] = Field(None, description="Creation timestamp")
    last_edited_time: Optional[str] = Field(None, description="Last edit timestamp")
    error: Optional[str] = Field(None, description="Error message if failed")
    error_type: Optional[str] = Field(None, description="Error type if failed")


class NotionPageData(BaseModel):
    """Notion page data schema."""

    id: str = Field(..., description="Page ID")
    created_time: str = Field(..., description="Creation timestamp")
    last_edited_time: str = Field(..., description="Last edit timestamp")
    properties: Dict[str, Any] = Field(..., description="Page properties")


class NotionQueryResult(BaseModel):
    """Notion database query result schema."""

    success: bool = Field(..., description="Whether the request was successful")
    total_count: Optional[int] = Field(None, description="Number of pages returned")
    has_more: Optional[bool] = Field(None, description="Whether there are more results")
    pages: Optional[List[NotionPageData]] = Field(None, description="List of pages")
    error: Optional[str] = Field(None, description="Error message if failed")
    error_type: Optional[str] = Field(None, description="Error type if failed")
