"""
RAW 레이어 스키마 정의.

Notion에서 가져온 원본 메모를 나타내는 Pydantic 모델.
"""

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class RawNoteCreate(BaseModel):
    """RAW note 생성 스키마."""

    notion_page_id: str = Field(..., description="Notion page ID")
    notion_url: str = Field(..., description="Notion page URL")
    title: Optional[str] = Field(None, description="메모 제목")
    content: Optional[str] = Field(None, description="메모 본문")
    properties_json: Dict[str, Any] = Field(
        default_factory=dict, description="Notion properties"
    )
    notion_created_time: datetime = Field(..., description="Notion 생성 시각")
    notion_last_edited_time: datetime = Field(..., description="Notion 수정 시각")


class RawNote(BaseModel):
    """RAW note 조회 스키마."""

    id: UUID = Field(..., description="DB UUID")
    notion_page_id: str
    notion_url: str
    title: Optional[str]
    content: Optional[str]
    properties_json: Dict[str, Any]
    notion_created_time: datetime
    notion_last_edited_time: datetime
    imported_at: datetime = Field(..., description="DB 저장 시각")

    class Config:
        from_attributes = True


class ImportResult(BaseModel):
    """Notion import 결과."""

    success: bool
    imported_count: int = Field(default=0, description="신규 import된 메모 수")
    skipped_count: int = Field(default=0, description="중복으로 skip된 메모 수")
    errors: list[str] = Field(default_factory=list, description="에러 메시지 목록")
