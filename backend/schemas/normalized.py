"""
NORMALIZED 레이어 스키마.

사고 단위(ThoughtUnit) 모델 정의.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ThoughtUnit(BaseModel):
    """
    사고 단위 (claim + context).

    메모에서 추출된 독립적인 사고 단위.
    """

    claim: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description="핵심 주장/아이디어 (10-500자)",
    )
    context: Optional[str] = Field(
        None, max_length=200, description="맥락/배경 정보 (선택, 최대 200자)"
    )

    model_config = {"json_schema_extra": {"example": {
        "claim": "사람은 자신이 하고 있는 일로 정의된다. 무엇을 하느냐가 곧 그 사람의 정체성이다.",
        "context": "직업과 정체성의 관계에 대한 생각",
    }}}


class ThoughtExtractionResult(BaseModel):
    """LLM이 반환하는 사고 단위 추출 결과."""

    thoughts: list[ThoughtUnit] = Field(
        ..., min_length=1, max_length=5, description="추출된 사고 단위 목록 (1-5개)"
    )

    model_config = {"json_schema_extra": {"example": {
        "thoughts": [
            {
                "claim": "사람은 자신이 하고 있는 일로 정의된다",
                "context": "직업과 정체성",
            },
            {
                "claim": "일을 통해 자아를 실현한다",
                "context": "자아실현의 방법",
            },
        ]
    }}}


class ThoughtUnitCreate(BaseModel):
    """DB에 저장할 사고 단위 데이터."""

    raw_note_id: str = Field(..., description="원본 메모 UUID")
    claim: str = Field(..., min_length=10, max_length=500)
    context: Optional[str] = Field(None, max_length=200)
    embedding: Optional[list[float]] = Field(
        None, description="임베딩 벡터 (1536 차원)"
    )
    embedding_model: str = Field(
        default="text-embedding-3-small", description="임베딩 모델명"
    )


class ThoughtUnitDB(ThoughtUnitCreate):
    """DB에서 조회한 사고 단위 (ID 포함)."""

    id: int
    extracted_at: datetime

    model_config = {"from_attributes": True}
