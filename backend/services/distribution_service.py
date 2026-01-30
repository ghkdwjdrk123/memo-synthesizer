"""
유사도 분포 계산 및 상대적 임계값 제공 서비스

상대적 임계값 전략 (P10-P40):
- 절대값 하드코딩 제거
- 데이터 특성에 맞게 자동 조정
- 캐싱으로 성능 최적화
"""

from typing import Optional, Tuple, Dict, Any
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class DistributionService:
    """유사도 분포 계산 및 상대적 임계값 제공"""

    def __init__(self, supabase_service):
        self.supabase = supabase_service
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_timestamp: Optional[datetime] = None
        self._memory_cache_ttl = timedelta(minutes=5)
        self._db_cache_ttl = timedelta(days=7)  # 7일 TTL (Distance Table 재계산 타임아웃 방지)

    async def get_distribution(
        self,
        force_recalculate: bool = False
    ) -> Dict[str, Any]:
        """
        분포 캐시 조회 (자동 갱신)

        캐싱 전략:
            - 메모리 캐시: 5분 TTL
            - DB 캐시: 24시간 TTL
            - 재계산 트리거: 24시간 경과 OR 데이터 10% 변화

        Returns:
            {
                "thought_count": 1921,
                "total_pairs": 38420,
                "percentiles": {"p0": 0.26, "p10": 0.30, ...},
                "mean": 0.38,
                "stddev": 0.05,
                "calculated_at": "2026-01-26T10:00:00",
                "duration_ms": 5432
            }
        """
        # 1. 메모리 캐시 확인
        if not force_recalculate and self._is_memory_cache_valid():
            logger.info("Distribution cache hit (memory)")
            return self._cache

        # 2. DB 캐시 확인
        db_cache = await self.supabase.get_similarity_distribution_cache()

        # 3. 재계산 필요 여부 판단
        needs_recalc = (
            force_recalculate or
            db_cache is None or
            self._is_db_cache_stale(db_cache) or
            await self._data_changed_significantly(db_cache)
        )

        if needs_recalc:
            logger.info("Recalculating similarity distribution from Distance Table...")
            result = await self.supabase.calculate_distribution_from_distance_table()

            if not result.get("success"):
                raise Exception(f"Failed to calculate distribution: {result.get('error')}")

            # DB 캐시 다시 조회
            db_cache = await self.supabase.get_similarity_distribution_cache()
        else:
            logger.info("Distribution cache hit (DB)")

        # 4. 메모리 캐시 갱신
        self._cache = db_cache
        self._cache_timestamp = datetime.now()

        return db_cache

    async def get_relative_thresholds(
        self,
        strategy: str = "p10_p40",
        custom_range: Optional[Tuple[int, int]] = None
    ) -> Tuple[float, float]:
        """
        상대적 임계값 계산

        전략:
            - "p10_p40": 하위 10-40% 구간 (기본, 창의적 조합)
            - "p30_p60": 하위 30-60% 구간 (안전한 연결)
            - "p0_p30": 최하위 30% (매우 다른 아이디어)
            - "custom": custom_range 사용 (예: (20, 50) → P20-P50)

        Returns:
            (min_similarity, max_similarity)

        Example:
            >>> await get_relative_thresholds("p10_p40")
            (0.28, 0.34)
        """
        dist = await self.get_distribution()
        percentiles = dist["percentiles"]

        if strategy == "custom":
            if not custom_range:
                raise ValueError("custom_range required for custom strategy")
            min_pct, max_pct = custom_range
            min_key = f"p{min_pct}"
            max_key = f"p{max_pct}"
        elif strategy == "p10_p40":
            min_key, max_key = "p10", "p40"
        elif strategy == "p30_p60":
            min_key, max_key = "p30", "p60"
        elif strategy == "p0_p30":
            min_key, max_key = "p0", "p30"
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

        min_similarity = percentiles.get(min_key)
        max_similarity = percentiles.get(max_key)

        if min_similarity is None or max_similarity is None:
            raise ValueError(f"Invalid percentile keys: {min_key}, {max_key}")

        logger.info(
            f"Relative thresholds ({strategy}): "
            f"{min_similarity:.3f} - {max_similarity:.3f}"
        )

        return (min_similarity, max_similarity)

    def _is_memory_cache_valid(self) -> bool:
        """메모리 캐시가 유효한지 확인 (5분 TTL)"""
        if self._cache is None or self._cache_timestamp is None:
            return False
        age = datetime.now() - self._cache_timestamp
        return age < self._memory_cache_ttl

    def _is_db_cache_stale(self, db_cache: Dict[str, Any]) -> bool:
        """DB 캐시가 오래되었는지 확인 (24시간 TTL)"""
        calculated_at_str = db_cache.get("calculated_at")
        if not calculated_at_str:
            return True

        # ISO 형식 문자열을 datetime으로 변환
        if isinstance(calculated_at_str, str):
            calculated_at = datetime.fromisoformat(calculated_at_str.replace('Z', '+00:00'))
        else:
            calculated_at = calculated_at_str

        age = datetime.now() - calculated_at.replace(tzinfo=None)
        return age > self._db_cache_ttl

    async def _data_changed_significantly(
        self,
        db_cache: Dict[str, Any]
    ) -> bool:
        """데이터가 크게 변경되었는지 확인 (10% 임계값)"""
        current_count = await self.supabase.count_thought_units()
        cached_count = db_cache.get("thought_count", 0)

        if cached_count == 0:
            return True

        change_ratio = abs(current_count - cached_count) / cached_count
        return change_ratio > 0.1  # 10% 이상 변화


# ============================================================
# Dependency Injection
# ============================================================

_distribution_service: Optional["DistributionService"] = None


def get_distribution_service():
    """
    DistributionService 싱글톤 인스턴스 반환

    FastAPI Depends에서 사용

    Usage:
        @router.get("/endpoint")
        async def endpoint(
            dist_service: DistributionService = Depends(get_distribution_service)
        ):
            ...
    """
    from backend.services.supabase_service import get_supabase_service

    global _distribution_service
    if _distribution_service is None:
        supabase_service = get_supabase_service()
        _distribution_service = DistributionService(supabase_service)
    return _distribution_service
