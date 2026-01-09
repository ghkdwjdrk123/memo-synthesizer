"""
AI API endpoints for testing embeddings and content generation.
"""

from fastapi import APIRouter, Depends, Body

from services.ai_service import AIService, get_ai_service
from schemas.ai import EmbeddingResponse, ContentGenerationResponse, TopicRecommendationResponse

router = APIRouter(prefix="/ai", tags=["AI"])


@router.post(
    "/embedding/test",
    response_model=EmbeddingResponse,
    summary="Test OpenAI Embedding",
    description="Test OpenAI embedding API with sample text."
)
async def test_embedding(
    text: str = Body(default="안녕하세요, 임베딩 테스트입니다.", embed=True),
    ai_service: AIService = Depends(get_ai_service)
) -> EmbeddingResponse:
    """
    Test OpenAI embedding API.

    Args:
        text: Text to embed

    Returns:
        EmbeddingResponse: Embedding result
    """
    result = await ai_service.create_embedding(text)
    return EmbeddingResponse(**result)


@router.post(
    "/claude/test",
    response_model=ContentGenerationResponse,
    summary="Test Claude Content Generation",
    description="Test Anthropic Claude API with sample prompt."
)
async def test_claude(
    prompt: str = Body(default="안녕하세요! 간단한 인사말을 해주세요.", embed=True),
    ai_service: AIService = Depends(get_ai_service)
) -> ContentGenerationResponse:
    """
    Test Claude content generation API.

    Args:
        prompt: Prompt for Claude

    Returns:
        ContentGenerationResponse: Generated content
    """
    result = await ai_service.generate_content_with_claude(prompt)
    return ContentGenerationResponse(**result)


@router.post(
    "/recommend-topics",
    response_model=TopicRecommendationResponse,
    summary="Recommend Writing Topics",
    description="Get writing topic recommendations based on Notion memos using Claude."
)
async def recommend_topics(
    max_topics: int = Body(default=5, embed=True, ge=1, le=10),
    ai_service: AIService = Depends(get_ai_service)
) -> TopicRecommendationResponse:
    """
    Recommend writing topics based on Notion database memos.

    Args:
        max_topics: Maximum number of topics to recommend (1-10)

    Returns:
        TopicRecommendationResponse: Recommended topics
    """
    # Notion 서비스에서 메모 가져오기
    from services.notion_service import get_notion_service
    notion_service = get_notion_service()

    query_result = await notion_service.query_database(page_size=20)

    if not query_result.get("success"):
        return TopicRecommendationResponse(
            success=False,
            error="Failed to fetch memos from Notion",
            error_type="NotionError"
        )

    memos = query_result.get("pages", [])
    if not memos:
        return TopicRecommendationResponse(
            success=False,
            error="No memos found in database",
            error_type="NoDataError"
        )

    # 메모 데이터를 AI 서비스 형식으로 변환
    memo_list = []
    for page in memos:
        props = page.get("properties", {})
        memo_list.append({
            "제목": props.get("제목", ""),
            "본문": props.get("본문", ""),
            "키워드": props.get("키워드", [])
        })

    result = await ai_service.recommend_topics(memo_list, max_topics=max_topics)
    return TopicRecommendationResponse(**result)
