"""
Pydantic schemas for Essay layer - generated writing prompts.
"""

from datetime import datetime
from pydantic import BaseModel, Field


class UsedThought(BaseModel):
    """에세이에 사용된 사고 단위"""
    thought_id: int
    claim: str
    source_title: str
    source_url: str = Field(..., pattern=r'^https?://', description="Notion page URL")


class EssayCreate(BaseModel):
    """에세이 생성 요청"""
    type: str = Field(default="essay", description="Type of content (essay, article, etc.)")
    title: str = Field(
        ...,
        min_length=5,
        max_length=100,
        description="Essay title"
    )
    outline: list[str] = Field(
        ...,
        min_length=3,
        max_length=3,
        description="3-part outline structure"
    )
    used_thoughts: list[UsedThought] = Field(
        ...,
        min_length=1,
        description="Thoughts used in this essay"
    )
    reason: str = Field(
        ...,
        max_length=300,
        description="Why this combination creates interesting writing prompt"
    )
    pair_id: int = Field(..., description="Source thought pair ID")


class EssayDB(EssayCreate):
    """DB 조회 모델"""
    id: int
    generated_at: datetime

    model_config = {"from_attributes": True}


class EssayResponse(BaseModel):
    """API 응답용 모델"""
    id: int
    type: str
    title: str
    outline: list[str]
    used_thoughts: list[UsedThought]
    reason: str
    pair_id: int
    generated_at: datetime

    model_config = {"from_attributes": True}


class EssayListResponse(BaseModel):
    """Essay 목록 응답"""
    total: int
    essays: list[EssayResponse]
