"""
Pydantic schemas for ZK (Zettelkasten) layer - thought pairs.
"""

from datetime import datetime
from pydantic import BaseModel, Field


class ThoughtPairCandidate(BaseModel):
    """유사도 계산 결과 (후보 쌍)"""
    thought_a_id: int
    thought_b_id: int
    thought_a_claim: str
    thought_b_claim: str
    similarity_score: float = Field(..., ge=0, le=1, description="Cosine similarity (0-1)")


class PairScoringRequest(BaseModel):
    """Claude에게 보낼 평가 요청"""
    pairs: list[ThoughtPairCandidate] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Pairs to evaluate (max 20 for batch processing)"
    )


class PairScore(BaseModel):
    """Claude가 반환하는 단일 쌍 점수"""
    thought_a_id: int
    thought_b_id: int
    logical_expansion_score: int = Field(
        ...,
        ge=0,
        le=100,
        description="Creative connection potential score (0-100)"
    )
    connection_reason: str = Field(
        ...,
        min_length=10,
        max_length=300,
        description="Why these thoughts connect creatively"
    )


class PairScoringResult(BaseModel):
    """Claude 평가 결과 (여러 쌍)"""
    pair_scores: list[PairScore] = Field(
        ...,
        min_length=1,
        description="Scores for all evaluated pairs"
    )


class ThoughtPairCreate(BaseModel):
    """DB 저장용 모델"""
    thought_a_id: int = Field(..., description="First thought ID (a < b)")
    thought_b_id: int = Field(..., description="Second thought ID (a < b)")
    similarity_score: float = Field(..., ge=0, le=1, description="Cosine similarity")
    connection_reason: str = Field(
        ...,
        max_length=500,
        description="Claude-generated connection reason"
    )


class ThoughtPairDB(ThoughtPairCreate):
    """DB 조회 모델"""
    id: int
    selected_at: datetime
    is_used_in_essay: bool = False

    model_config = {"from_attributes": True}


class ThoughtPairResponse(BaseModel):
    """API 응답용 모델 (상세 정보 포함)"""
    id: int
    thought_a_id: int
    thought_b_id: int
    similarity_score: float
    connection_reason: str
    logical_expansion_score: int | None = None
    is_used_in_essay: bool
    selected_at: datetime

    model_config = {"from_attributes": True}


# ============================================================================
# 하이브리드 C 전략: pair_candidates 관련 스키마
# ============================================================================


class PairCandidateCreate(BaseModel):
    """pair_candidates 테이블 저장용 모델"""
    thought_a_id: int = Field(..., description="First thought ID (a < b)")
    thought_b_id: int = Field(..., description="Second thought ID (a < b)")
    similarity: float = Field(..., ge=0, le=1, description="Cosine similarity (1 - cosine_distance)")
    raw_note_id_a: str = Field(..., description="Source note ID for thought A (UUID)")
    raw_note_id_b: str = Field(..., description="Source note ID for thought B (UUID)")


class PairCandidateDB(PairCandidateCreate):
    """pair_candidates DB 조회 모델"""
    id: int
    llm_score: int | None = Field(None, ge=0, le=100, description="Claude evaluation score (NULL = not evaluated)")
    llm_status: str = Field(default="pending", description="LLM evaluation status: pending | processing | completed | failed")
    llm_attempts: int = Field(default=0, ge=0, le=3, description="Retry count (0-3)")
    last_evaluated_at: datetime | None = None
    evaluation_error: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class PairCandidateBatch(BaseModel):
    """배치 저장 응답 모델"""
    inserted_count: int = Field(..., description="Number of successfully inserted pairs")
    duplicate_count: int = Field(..., description="Number of duplicate pairs (skipped)")
    error_count: int = Field(..., description="Number of failed insertions")


class ThoughtPairCreateExtended(BaseModel):
    """확장된 thought_pairs 저장용 모델 (claude_score, quality_tier 포함)"""
    thought_a_id: int = Field(..., description="First thought ID (a < b)")
    thought_b_id: int = Field(..., description="Second thought ID (a < b)")
    similarity_score: float = Field(..., ge=0, le=1, description="Cosine similarity")
    connection_reason: str = Field(
        ...,
        max_length=500,
        description="Claude-generated connection reason"
    )
    claude_score: int | None = Field(None, ge=0, le=100, description="Claude evaluation score (0-100)")
    quality_tier: str | None = Field(
        None,
        pattern="^(standard|premium|excellent)$",
        description="Quality tier: standard(65-84) | premium(85-94) | excellent(95-100)"
    )
    essay_content: str | None = Field(None, description="Optional pre-generated essay content for UI preview")


class ThoughtPairDBExtended(ThoughtPairCreateExtended):
    """확장된 thought_pairs DB 조회 모델"""
    id: int
    selected_at: datetime
    is_used_in_essay: bool = False

    model_config = {"from_attributes": True}


class PairCandidateWithThoughts(BaseModel):
    """thought 내용이 포함된 후보 페어 (배치 워커용)"""
    id: int
    thought_a_id: int
    thought_b_id: int
    thought_a_claim: str
    thought_b_claim: str
    similarity: float
    raw_note_id_a: str
    raw_note_id_b: str

    model_config = {"from_attributes": True}
