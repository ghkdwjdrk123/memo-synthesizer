"""
유사도 분포 계산 및 상대적 임계값 제공 서비스

상대적 임계값 전략 (P10-P40):
- 절대값 하드코딩 제거
- 데이터 특성에 맞게 자동 조정
- 캐싱으로 성능 최적화

변경 이력:
- 2026-02: Distance Table 방식 → 샘플링 기반 스케치 방식으로 전환
"""

from typing import Optional, Tuple, Dict, Any
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class DistributionService:
    """유사도 분포 계산 및 상대적 임계값 제공"""

    # 기본 분포값 (스케치 미존재 시 폴백)
    DEFAULT_DISTRIBUTION = {
        "thought_count": 0,
        "total_pairs": 0,
        "percentiles": {
            "p0": 0.0, "p10": 0.05, "p20": 0.10, "p30": 0.15,
            "p40": 0.20, "p50": 0.25, "p60": 0.30, "p70": 0.35,
            "p80": 0.40, "p90": 0.50, "p100": 1.0
        },
        "mean": 0.25,
        "stddev": 0.15,
        "calculated_at": None,
        "duration_ms": 0,
        "is_approximate": True,
        "is_default": True
    }

    def __init__(self, supabase_service):
        self.supabase = supabase_service
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_timestamp: Optional[datetime] = None
        self._memory_cache_ttl = timedelta(minutes=5)
        self._db_cache_ttl = timedelta(days=7)  # 7일 TTL

    async def get_distribution(
        self,
        force_recalculate: bool = False
    ) -> Dict[str, Any]:
        """
        분포 캐시 조회 (자동 갱신)

        캐싱 전략:
            - 메모리 캐시: 5분 TTL
            - DB 캐시: 7일 TTL
            - 재계산 트리거: force_recalculate=True OR 캐시 없음 OR 7일 경과

        변경사항 (2026-02):
            - Distance Table → 샘플링 기반 스케치 방식으로 전환
            - is_approximate=True 필드 추가 (근사값임을 명시)

        Returns:
            {
                "thought_count": 1921,
                "total_pairs": 100000,  # 샘플 수 (전쌍 아님)
                "percentiles": {"p0": 0.26, "p10": 0.30, ...},
                "mean": 0.38,
                "stddev": 0.05,
                "calculated_at": "2026-01-26T10:00:00",
                "duration_ms": 5432,
                "is_approximate": True
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
            logger.info("Recalculating similarity distribution from sketch...")
            result = await self.supabase.calculate_distribution_from_sketch()

            if not result.get("success"):
                # 스케치가 없는 경우 기본값 반환 + 경고
                error_msg = result.get("error", "Unknown error")
                logger.warning(
                    f"Distribution calculation failed: {error_msg}. "
                    f"Using default distribution. "
                    f"Run build_distribution_sketch() to create samples."
                )

                # 기본값 반환
                default_dist = self.DEFAULT_DISTRIBUTION.copy()
                default_dist["calculated_at"] = datetime.now().isoformat()

                self._cache = default_dist
                self._cache_timestamp = datetime.now()
                return default_dist

            # DB 캐시 다시 조회
            db_cache = await self.supabase.get_similarity_distribution_cache()

            if db_cache is None:
                logger.warning("DB cache still empty after recalculation")
                return self.DEFAULT_DISTRIBUTION.copy()
        else:
            logger.info("Distribution cache hit (DB)")

        # 4. 메모리 캐시 갱신 + is_approximate 추가
        db_cache["is_approximate"] = True
        self._cache = db_cache
        self._cache_timestamp = datetime.now()

        return db_cache

    async def build_sketch(
        self,
        seed: int = 42,
        src_sample: int = 200,
        dst_sample: int = 500,
        rounds: int = 1,
        exclude_same_memo: bool = True
    ) -> Dict[str, Any]:
        """
        전역 분포 스케치 빌드 (샘플 수집)

        Args:
            seed: 결정론적 샘플링용 시드
            src_sample: src 샘플 크기
            dst_sample: dst 샘플 크기
            rounds: 샘플링 라운드 수

        Returns:
            {
                "success": bool,
                "run_id": str,
                "inserted_samples": int,
                "duration_ms": int
            }
        """
        logger.info(
            f"Building distribution sketch: "
            f"src={src_sample}, dst={dst_sample}, rounds={rounds}"
        )

        result = await self.supabase.build_distribution_sketch(
            p_seed=seed,
            p_src_sample=src_sample,
            p_dst_sample=dst_sample,
            p_rounds=rounds,
            p_exclude_same_memo=exclude_same_memo
        )

        if result.get("success"):
            logger.info(
                f"Sketch built: {result.get('inserted_samples')} samples, "
                f"run_id={result.get('run_id')}"
            )
        else:
            logger.error(f"Sketch build failed: {result.get('error')}")

        return result

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
        """DB 캐시가 오래되었는지 확인 (7일 TTL)"""
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
    from services.supabase_service import get_supabase_service

    global _distribution_service
    if _distribution_service is None:
        supabase_service = get_supabase_service()
        _distribution_service = DistributionService(supabase_service)
    return _distribution_service
