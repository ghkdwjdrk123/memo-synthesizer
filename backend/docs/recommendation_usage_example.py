"""
RecommendationEngine 사용 예시.

이 파일은 실제 실행 코드가 아닌 참고용 예시입니다.
"""

import asyncio
from services.supabase_service import get_supabase_service
from services.recommendation import RecommendationEngine


async def example_basic_usage():
    """기본 사용 예시 (모든 tier, 기본 가중치)"""
    supabase = get_supabase_service()
    engine = RecommendationEngine(supabase)

    # 기본 설정으로 10개 추천
    pairs = await engine.get_recommended_pairs(limit=10)

    print(f"총 {len(pairs)}개 추천")
    for i, pair in enumerate(pairs, 1):
        print(
            f"{i}. Pair #{pair['id']} - "
            f"Score: {pair['final_score']:.2f} "
            f"(Claude: {pair['claude_score']}, Diversity: {pair['diversity_score']:.4f})"
        )


async def example_excellent_only():
    """Excellent tier만 사용"""
    supabase = get_supabase_service()
    engine = RecommendationEngine(supabase)

    # Excellent tier만 조회
    pairs = await engine.get_recommended_pairs(
        limit=5,
        quality_tiers=["excellent"]
    )

    print(f"Excellent tier: {len(pairs)}개")


async def example_high_diversity():
    """다양성 우선 추천 (diversity_weight=0.7)"""
    supabase = get_supabase_service()
    engine = RecommendationEngine(supabase)

    # 다양성 가중치를 높게 설정
    pairs = await engine.get_recommended_pairs(
        limit=10,
        diversity_weight=0.7  # 70% 다양성, 30% Claude 점수
    )

    print(f"다양성 우선 추천: {len(pairs)}개")
    for pair in pairs[:3]:  # 상위 3개만 출력
        print(
            f"Pair #{pair['id']} - "
            f"Final: {pair['final_score']:.2f}, "
            f"Claude: {pair['claude_score']}, "
            f"Diversity: {pair['diversity_score']:.4f}"
        )


async def example_quality_only():
    """Claude 점수만 고려 (diversity_weight=0)"""
    supabase = get_supabase_service()
    engine = RecommendationEngine(supabase)

    # 다양성 무시, Claude 점수만 사용
    pairs = await engine.get_recommended_pairs(
        limit=10,
        diversity_weight=0.0  # 100% Claude 점수
    )

    print(f"품질 우선 추천: {len(pairs)}개")


async def example_fastapi_integration():
    """FastAPI 라우터 통합 예시"""
    from fastapi import APIRouter, Depends, Query
    from services.recommendation import get_recommendation_engine

    router = APIRouter(prefix="/recommendations", tags=["recommendations"])

    @router.get("/")
    async def get_recommendations(
        limit: int = Query(default=10, ge=1, le=100),
        quality_tiers: list[str] = Query(
            default=["excellent", "premium", "standard"]
        ),
        diversity_weight: float = Query(default=0.3, ge=0.0, le=1.0),
        supabase=Depends(get_supabase_service),
    ):
        """
        추천 페어 조회 API.

        Query Parameters:
            - limit: 반환 개수 (1-100, 기본 10)
            - quality_tiers: tier 목록 (기본: all)
            - diversity_weight: 다양성 가중치 (0-1, 기본 0.3)
        """
        engine = RecommendationEngine(supabase)

        pairs = await engine.get_recommended_pairs(
            limit=limit,
            quality_tiers=quality_tiers,
            diversity_weight=diversity_weight
        )

        return {
            "success": True,
            "count": len(pairs),
            "recommendations": pairs
        }

    # 사용 예시 (실제 요청)
    # GET /recommendations?limit=5&quality_tiers=excellent&quality_tiers=premium&diversity_weight=0.5


if __name__ == "__main__":
    # 예시 실행 (실제로는 FastAPI 서버에서 사용)
    print("=== 기본 사용 예시 ===")
    asyncio.run(example_basic_usage())

    print("\n=== Excellent tier만 ===")
    asyncio.run(example_excellent_only())

    print("\n=== 다양성 우선 ===")
    asyncio.run(example_high_diversity())

    print("\n=== 품질 우선 ===")
    asyncio.run(example_quality_only())
