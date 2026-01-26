"""
추천 엔진 서비스.

quality_tier 기반 Essay 추천 알고리즘 제공.
"""

import logging
from typing import List, Dict, Optional
from collections import defaultdict

from services.supabase_service import SupabaseService

logger = logging.getLogger(__name__)


class RecommendationEngine:
    """
    Essay 추천 엔진.

    Quality tier와 다양성을 고려한 추천 알고리즘 제공.
    """

    def __init__(self, supabase_service: SupabaseService):
        """
        추천 엔진 초기화.

        Args:
            supabase_service: Supabase 서비스 인스턴스
        """
        self.supabase = supabase_service

    async def get_recommended_pairs(
        self,
        limit: int = 10,
        quality_tiers: List[str] = None,
        diversity_weight: float = 0.3
    ) -> List[Dict]:
        """
        추천 페어 조회 (quality tier + 다양성 기반).

        알고리즘:
            1. thought_pairs에서 조회:
               - is_used_in_essay = FALSE
               - quality_tier IN quality_tiers
               - tier별 우선순위 (excellent → premium → standard)

            2. 다양성 스코어 계산:
               - raw_note_id 등장 횟수 카운트
               - diversity_score = 1 / (count_a + count_b)

            3. 최종 점수 계산:
               - final_score = (claude_score × (1 - diversity_weight)) +
                               (diversity_score × 100 × diversity_weight)

            4. 정렬 및 상위 N개 선택

        Args:
            limit: 반환 개수 (기본 10)
            quality_tiers: 우선순위 tier 목록 (기본: ["excellent", "premium", "standard"])
            diversity_weight: 다양성 가중치 (0-1, 기본 0.3)
                              0 = claude_score만 고려
                              1 = diversity_score만 고려

        Returns:
            추천 페어 목록 (final_score 포함, DESC 정렬)
            각 dict 구조:
            {
                "id": int,
                "thought_a_id": int,
                "thought_b_id": int,
                "similarity_score": float,
                "connection_reason": str,
                "claude_score": int,
                "quality_tier": str,
                "thought_a": {
                    "raw_note_id": str,
                    ...
                },
                "thought_b": {
                    "raw_note_id": str,
                    ...
                },
                "diversity_score": float,
                "final_score": float
            }

        Example:
            >>> engine = RecommendationEngine(supabase_service)
            >>> pairs = await engine.get_recommended_pairs(
            ...     limit=5,
            ...     quality_tiers=["excellent", "premium"],
            ...     diversity_weight=0.3
            ... )
            >>> print(f"Top pair score: {pairs[0]['final_score']}")
        """
        # 기본값 설정
        if quality_tiers is None:
            quality_tiers = ["excellent", "premium", "standard"]

        # 입력 검증
        if not (0 <= diversity_weight <= 1):
            logger.warning(
                f"Invalid diversity_weight {diversity_weight}, clamping to [0, 1]"
            )
            diversity_weight = max(0.0, min(1.0, diversity_weight))

        if limit <= 0:
            logger.warning(f"Invalid limit {limit}, using default 10")
            limit = 10

        valid_tiers = ["excellent", "premium", "standard"]
        quality_tiers = [t for t in quality_tiers if t in valid_tiers]

        if not quality_tiers:
            logger.warning("No valid quality_tiers provided, using all tiers")
            quality_tiers = valid_tiers

        logger.info(
            f"Getting recommended pairs: limit={limit}, "
            f"tiers={quality_tiers}, diversity_weight={diversity_weight:.2f}"
        )

        # Step 1: tier별 순차 조회 (우선순위 보장)
        all_pairs = []

        for tier in quality_tiers:
            try:
                response = await (
                    self.supabase.client.table("thought_pairs")
                    .select("""
                        id, thought_a_id, thought_b_id,
                        similarity_score, connection_reason,
                        claude_score, quality_tier,
                        thought_a:thought_units!thought_pairs_thought_a_id_fkey(raw_note_id),
                        thought_b:thought_units!thought_pairs_thought_b_id_fkey(raw_note_id)
                    """)
                    .eq("is_used_in_essay", False)
                    .eq("quality_tier", tier)
                    .not_.is_("claude_score", "null")  # claude_score가 NULL이 아닌 것만
                    .order("claude_score", desc=True)
                    .limit(limit * 2)  # 다양성 계산을 위해 넉넉하게
                    .execute()
                )

                if response.data:
                    all_pairs.extend(response.data)
                    logger.info(
                        f"Retrieved {len(response.data)} pairs from tier '{tier}'"
                    )
                else:
                    logger.info(f"No pairs found for tier '{tier}'")

            except Exception as e:
                logger.error(f"Failed to query tier '{tier}': {e}")
                # 특정 tier 실패해도 계속 진행
                continue

        if not all_pairs:
            logger.warning("No eligible pairs found")
            return []

        logger.info(f"Total pairs retrieved: {len(all_pairs)}")

        # Step 2: 다양성 스코어 계산
        scored_pairs = self._calculate_diversity_scores(all_pairs, diversity_weight)

        # Step 3: 정렬 및 상위 N개
        scored_pairs.sort(key=lambda x: x["final_score"], reverse=True)
        result = scored_pairs[:limit]

        logger.info(
            f"Returning {len(result)} recommended pairs "
            f"(top score: {result[0]['final_score']:.2f}, "
            f"bottom score: {result[-1]['final_score']:.2f})"
        )

        return result

    def _calculate_diversity_scores(
        self,
        pairs: List[Dict],
        diversity_weight: float
    ) -> List[Dict]:
        """
        다양성 스코어 계산 및 최종 점수 계산.

        알고리즘:
            1. raw_note_id 등장 횟수 카운트
            2. diversity_score = 1 / (count_a + count_b)
            3. final_score = claude_score × (1-w) + diversity_score × 100 × w

        Args:
            pairs: 페어 목록 (thought_a, thought_b 포함)
            diversity_weight: 다양성 가중치 (0-1)

        Returns:
            diversity_score와 final_score가 추가된 페어 목록

        Note:
            - 자주 등장하는 raw_note일수록 diversity_score가 낮음
            - claude_score와 diversity_score를 가중 평균으로 결합
        """
        # Step 1: raw_note_id 등장 횟수 카운트
        note_counts: Dict[str, int] = defaultdict(int)

        for pair in pairs:
            # thought_a와 thought_b는 JOIN 결과 (dict)
            thought_a = pair.get("thought_a")
            thought_b = pair.get("thought_b")

            if not thought_a or not thought_b:
                logger.warning(
                    f"Pair {pair.get('id')} missing thought_a or thought_b, skipping"
                )
                continue

            raw_note_id_a = thought_a.get("raw_note_id")
            raw_note_id_b = thought_b.get("raw_note_id")

            if raw_note_id_a:
                note_counts[raw_note_id_a] += 1
            if raw_note_id_b:
                note_counts[raw_note_id_b] += 1

        logger.debug(f"Note usage counts: {len(note_counts)} unique notes")

        # Step 2: 각 페어의 스코어 계산
        for pair in pairs:
            thought_a = pair.get("thought_a")
            thought_b = pair.get("thought_b")

            if not thought_a or not thought_b:
                # 스킵 (이미 경고 로그 출력됨)
                pair["diversity_score"] = 0.0
                pair["final_score"] = 0.0
                continue

            raw_note_id_a = thought_a.get("raw_note_id")
            raw_note_id_b = thought_b.get("raw_note_id")

            # 카운트 가져오기 (없으면 1)
            count_a = note_counts.get(raw_note_id_a, 1)
            count_b = note_counts.get(raw_note_id_b, 1)

            # 다양성 스코어 계산 (0-1 범위)
            diversity_score = 1.0 / (count_a + count_b)

            # Claude 점수 (0-100)
            claude_score = pair.get("claude_score", 0)

            if claude_score is None:
                logger.warning(
                    f"Pair {pair.get('id')} has NULL claude_score, treating as 0"
                )
                claude_score = 0

            # 최종 점수 계산
            # claude_score는 0-100이므로 diversity_score를 100배해서 동일 스케일로 맞춤
            final_score = (
                claude_score * (1 - diversity_weight) +
                (diversity_score * 100) * diversity_weight
            )

            # 결과 저장
            pair["diversity_score"] = diversity_score
            pair["final_score"] = final_score

            logger.debug(
                f"Pair {pair.get('id')}: claude={claude_score}, "
                f"diversity={diversity_score:.4f}, final={final_score:.2f}"
            )

        return pairs


# 싱글톤 인스턴스 (의존성 주입용)
_recommendation_engine: Optional[RecommendationEngine] = None


def get_recommendation_engine(
    supabase_service: SupabaseService
) -> RecommendationEngine:
    """
    추천 엔진 싱글톤 인스턴스 반환.

    FastAPI Depends에서 사용.

    Args:
        supabase_service: Supabase 서비스 인스턴스

    Returns:
        RecommendationEngine 인스턴스

    Example:
        >>> from fastapi import Depends
        >>>
        >>> @router.get("/recommendations")
        >>> async def get_recommendations(
        ...     engine: RecommendationEngine = Depends(get_recommendation_engine)
        ... ):
        ...     return await engine.get_recommended_pairs(limit=10)
    """
    global _recommendation_engine

    if _recommendation_engine is None:
        _recommendation_engine = RecommendationEngine(supabase_service)

    return _recommendation_engine
