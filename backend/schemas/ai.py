"""
AI-related schemas.
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class EmbeddingResponse(BaseModel):
    """Embedding response schema."""

    success: bool = Field(..., description="Whether the request was successful")
    embedding: Optional[list[float]] = Field(None, description="Embedding vector")
    dimension: Optional[int] = Field(None, description="Embedding dimension")
    model: Optional[str] = Field(None, description="Model used")
    tokens_used: Optional[int] = Field(None, description="Number of tokens used")
    error: Optional[str] = Field(None, description="Error message if failed")
    error_type: Optional[str] = Field(None, description="Error type if failed")


class ContentGenerationResponse(BaseModel):
    """Content generation response schema."""

    success: bool = Field(..., description="Whether the request was successful")
    content: Optional[str] = Field(None, description="Generated content")
    model: Optional[str] = Field(None, description="Model used")
    tokens_used: Optional[Dict[str, int]] = Field(None, description="Tokens used (input/output)")
    stop_reason: Optional[str] = Field(None, description="Why generation stopped")
    error: Optional[str] = Field(None, description="Error message if failed")
    error_type: Optional[str] = Field(None, description="Error type if failed")


class TopicRecommendationResponse(BaseModel):
    """Topic recommendation response schema."""

    success: bool = Field(..., description="Whether the request was successful")
    content: Optional[str] = Field(None, description="Recommended topics")
    model: Optional[str] = Field(None, description="Model used")
    tokens_used: Optional[Dict[str, int]] = Field(None, description="Tokens used")
    stop_reason: Optional[str] = Field(None, description="Why generation stopped")
    error: Optional[str] = Field(None, description="Error message if failed")
    error_type: Optional[str] = Field(None, description="Error type if failed")
