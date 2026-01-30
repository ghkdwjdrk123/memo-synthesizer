# 상대적 임계값 기반 전략으로 전환: 구현 계획

## 문서 정보
- **작성일**: 2026-01-26
- **목적**: Perplexity 리서치 결과를 바탕으로 절대값 기반 유사도 임계값(0.05-0.35)을 상대적 분포 기반 임계값으로 전환
- **현재 상태**: 1921개 thought_units, 실제 유사도 분포 0.26-0.50
- **핵심 문제**: 절대값 임계값으로는 후보 페어를 찾을 수 없음

---

## 1. 현재 구현 분석

### 1.1 데이터 현황
- **Thought units**: 1,921개
- **실제 유사도 분포**: 0.26 ~ 0.50 (한국어, 동일 저자, text-embedding-3-small 특성)
- **현재 임계값**: 0.05 ~ 0.35 (절대값)
- **문제**: 분포와 임계값 불일치 → 0개 후보 발견

### 1.2 현재 아키텍처 (Hybrid C) - 올바름, 변경 불필요
```
Step 1: collect-candidates
  ↓ find_candidate_pairs (Top-K RPC, 0.05-0.35 고정)
  ↓ insert_pair_candidates_batch
  → pair_candidates 테이블 (30,000개 목표)

Step 2: sample-initial
  ↓ get_pending_candidates
  ↓ SamplingStrategy.sample_initial (3-tier: Low/Mid/High)
  ↓ BatchEvaluationWorker.run_batch
  → thought_pairs 테이블 (고득점 이동)

Step 3: score-candidates (백그라운드)
  ↓ BatchEvaluationWorker.run_batch
  → 나머지 후보 평가
```

**결론**: 아키텍처는 올바름. 문제는 임계값 정의 방식.

### 1.3 현재 SamplingStrategy (절대값 기반) - 수정 필요
```python
# backend/services/sampling.py
class SamplingStrategy:
    def __init__(
        self,
        low_range: Tuple[float, float] = (0.05, 0.15),   # ❌ 하드코딩
        mid_range: Tuple[float, float] = (0.15, 0.25),   # ❌ 하드코딩
        high_range: Tuple[float, float] = (0.25, 0.35),  # ❌ 하드코딩
```

**문제점**:
- 0.05-0.35 범위가 실제 데이터(0.26-0.50)와 불일치
- 데이터 특성(언어, 저자, 모델)을 고려하지 않음
- Fallback 전략도 절대값 기반

---

## 2. Perplexity 핵심 발견사항 요약

### 2.1 현재 분포는 정상
- text-embedding-3-small + 한국어 + 동일 저자 → 0.26-0.50 범위는 예상된 결과
- **문제가 아니라 데이터 특성**

### 2.2 약한 연결 정의 변경 필요
- **기존**: 절대값 0.05-0.35 (하드코딩)
- **개선**: 상대적 기준 (전체 분포 대비)
  - 옵션 A: 하위 30-60% 구간 (낮은 유사도 = 약한 연결)
  - 옵션 B: 상위 10-30% 구간 (높은 유사도 제외)

### 2.3 제안된 개선사항
1. **전체 페어 분포 계산 및 캐싱** (필수) ← 이 계획의 핵심
2. 사고 추출 프롬프트 개선 (다양성 확보)
3. 데이터 다양화 (다른 저자, 주제)
4. Negative sampling 실험

---

## 3. 설계 목표

### 3.1 핵심 원칙
- **자동 적응**: 데이터 특성에 맞게 임계값이 자동으로 조정
- **명확한 의미**: "약한 연결 = 하위 10-40% 유사도 구간" (창의적 조합)
- **성능 최적화**: 분포 계산 비용 vs 캐싱 전략 균형
- **하위 호환성**: 기존 코드 최소 수정

### 3.2 상대적 임계값 정의
```
전체 페어 유사도 분포: [0.26 ──────────────── 0.50]
                         P0    P10    P40    P100

"약한 연결" = P10 ~ P40 구간 (창의적 조합)
  예: P10 = 0.28, P40 = 0.34
  → min_similarity = 0.28, max_similarity = 0.34
```

**선택 기준**:
- P10-P40: 낮은 유사도 집중 (창의적 조합) ✅ 기본값
- P30-P60: 중간 구간 (안전한 조합)
- P0-P30: 최하위만 (매우 다른 아이디어)

→ **기본값: P10-P40** (창의적 조합 선호)

### 3.3 3-Tier 샘플링 재정의
```
기존 (절대값):
  Low: 0.05-0.15
  Mid: 0.15-0.25
  High: 0.25-0.35

개선 (백분위수 기반, P10-P40 전략):
  Low:  P10 - P20 (하위 10-20%, 가장 다른 아이디어)
  Mid:  P20 - P30 (하위 20-30%, 중간 정도)
  High: P30 - P40 (하위 30-40%, 약간 유사)

예시 (실제 0.26-0.50 분포):
  Low:  ~0.28 - 0.30  (약 3,842개 페어)
  Mid:  ~0.30 - 0.32  (약 3,842개 페어)
  High: ~0.32 - 0.34  (약 3,842개 페어)
```

**백분위수 기반 방식 선택 이유**:
- 각 Tier가 정확히 **전체의 10%씩** 차지 (균등 분포 보장)
- Mid와 High가 명확히 구분됨 (3등분 방식의 문제 해결)
- 샘플링 시 안정적인 다양성 확보

---

## 4. 구현 계획 (3개 Phase)

### Phase 1: 분포 계산 및 캐싱 인프라 (신규)

#### 1.1 새로운 테이블 생성 (SQL Migration)
**파일**: `backend/docs/supabase_migrations/008_create_similarity_distribution_cache.sql` (신규)

```sql
CREATE TABLE IF NOT EXISTS similarity_distribution_cache (
    id SERIAL PRIMARY KEY,

    -- 분포 통계
    thought_unit_count INTEGER NOT NULL,
    total_pair_count BIGINT NOT NULL,

    -- 백분위수 (Percentile) 저장
    p0 FLOAT NOT NULL,   -- 최소값
    p10 FLOAT NOT NULL,
    p20 FLOAT NOT NULL,
    p30 FLOAT NOT NULL,
    p40 FLOAT NOT NULL,
    p50 FLOAT NOT NULL,  -- 중간값 (median)
    p60 FLOAT NOT NULL,
    p70 FLOAT NOT NULL,
    p80 FLOAT NOT NULL,
    p90 FLOAT NOT NULL,
    p100 FLOAT NOT NULL, -- 최대값

    -- 평균 및 표준편차
    mean FLOAT NOT NULL,
    stddev FLOAT NOT NULL,

    -- 캐시 메타데이터
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    embedding_model TEXT NOT NULL DEFAULT 'text-embedding-3-small',
    calculation_duration_ms INTEGER,

    CONSTRAINT single_cache_row UNIQUE (id)
);

-- 초기 레코드 생성 (빈 값)
INSERT INTO similarity_distribution_cache (
    id, thought_unit_count, total_pair_count,
    p0, p10, p20, p30, p40, p50, p60, p70, p80, p90, p100,
    mean, stddev
) VALUES (
    1, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0
) ON CONFLICT (id) DO NOTHING;
```

**설계 근거**:
- 단일 레코드만 유지 (id=1 고정)
- 백분위수 사전 계산 → 쿼리 시 즉시 반환 (O(1))

#### 1.2 분포 계산 RPC 함수 (SQL Migration)
**파일**: `backend/docs/supabase_migrations/009_create_calculate_similarity_distribution.sql` (신규)

```sql
CREATE OR REPLACE FUNCTION calculate_similarity_distribution()
RETURNS jsonb AS $$
DECLARE
    v_start_time TIMESTAMPTZ;
    v_duration_ms INTEGER;
    v_thought_count INTEGER;
    v_total_pairs BIGINT;
    v_percentiles FLOAT[];
    v_mean FLOAT;
    v_stddev FLOAT;
BEGIN
    v_start_time := clock_timestamp();

    -- 1. thought_units 개수 확인
    SELECT COUNT(*) INTO v_thought_count
    FROM thought_units
    WHERE embedding IS NOT NULL;

    IF v_thought_count < 2 THEN
        RAISE EXCEPTION 'Not enough thought units (need >= 2, got %)', v_thought_count;
    END IF;

    -- 2. Top-K 기반 페어 샘플링 (성능 최적화)
    -- 각 thought당 top_k=20 → 최대 1921*20 = 38,420 페어
    DROP TABLE IF EXISTS temp_similarity_sample;

    CREATE TEMP TABLE temp_similarity_sample AS
    WITH ranked_pairs AS (
        SELECT
            a.id as thought_a_id,
            b.id as thought_b_id,
            1 - (a.embedding <=> b.embedding) as similarity,
            ROW_NUMBER() OVER (
                PARTITION BY a.id
                ORDER BY a.embedding <=> b.embedding
            ) as rn
        FROM thought_units a
        CROSS JOIN thought_units b
        WHERE a.id < b.id
          AND a.embedding IS NOT NULL
          AND b.embedding IS NOT NULL
    )
    SELECT similarity
    FROM ranked_pairs
    WHERE rn <= 20;  -- Top-K = 20

    GET DIAGNOSTICS v_total_pairs = ROW_COUNT;

    -- 3. 백분위수 계산 (PERCENTILE_CONT)
    SELECT ARRAY[
        PERCENTILE_CONT(0.0) WITHIN GROUP (ORDER BY similarity),
        PERCENTILE_CONT(0.1) WITHIN GROUP (ORDER BY similarity),
        PERCENTILE_CONT(0.2) WITHIN GROUP (ORDER BY similarity),
        PERCENTILE_CONT(0.3) WITHIN GROUP (ORDER BY similarity),
        PERCENTILE_CONT(0.4) WITHIN GROUP (ORDER BY similarity),
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY similarity),
        PERCENTILE_CONT(0.6) WITHIN GROUP (ORDER BY similarity),
        PERCENTILE_CONT(0.7) WITHIN GROUP (ORDER BY similarity),
        PERCENTILE_CONT(0.8) WITHIN GROUP (ORDER BY similarity),
        PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY similarity),
        PERCENTILE_CONT(1.0) WITHIN GROUP (ORDER BY similarity)
    ]
    INTO v_percentiles
    FROM temp_similarity_sample;

    -- 4. 평균 및 표준편차 계산
    SELECT AVG(similarity), STDDEV_POP(similarity)
    INTO v_mean, v_stddev
    FROM temp_similarity_sample;

    -- 5. 계산 소요 시간
    v_duration_ms := EXTRACT(EPOCH FROM (clock_timestamp() - v_start_time)) * 1000;

    -- 6. 캐시 테이블 업데이트 (UPSERT)
    UPDATE similarity_distribution_cache
    SET
        thought_unit_count = v_thought_count,
        total_pair_count = v_total_pairs,
        p0 = v_percentiles[1],
        p10 = v_percentiles[2],
        p20 = v_percentiles[3],
        p30 = v_percentiles[4],
        p40 = v_percentiles[5],
        p50 = v_percentiles[6],
        p60 = v_percentiles[7],
        p70 = v_percentiles[8],
        p80 = v_percentiles[9],
        p90 = v_percentiles[10],
        p100 = v_percentiles[11],
        mean = v_mean,
        stddev = v_stddev,
        calculated_at = NOW(),
        calculation_duration_ms = v_duration_ms
    WHERE id = 1;

    -- 7. 결과 반환
    RETURN jsonb_build_object(
        'success', true,
        'thought_count', v_thought_count,
        'total_pairs', v_total_pairs,
        'percentiles', jsonb_build_object(
            'p0', v_percentiles[1],
            'p10', v_percentiles[2],
            'p20', v_percentiles[3],
            'p30', v_percentiles[4],
            'p40', v_percentiles[5],
            'p50', v_percentiles[6],
            'p60', v_percentiles[7],
            'p70', v_percentiles[8],
            'p80', v_percentiles[9],
            'p90', v_percentiles[10],
            'p100', v_percentiles[11]
        ),
        'mean', v_mean,
        'stddev', v_stddev,
        'duration_ms', v_duration_ms
    );

EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', SQLERRM
        );
END;
$$ LANGUAGE plpgsql;
```

**설계 근거**:
- Top-K 샘플링으로 성능 최적화 (전체 O(n²) 회피)
- PERCENTILE_CONT: 정확한 백분위수 계산
- 예상 성능: ~5-10초 (1921 thoughts × 20 = 38,420 페어)

#### 1.3 Python 서비스 계층 추가
**파일**: `backend/services/distribution_service.py` (신규)

**핵심 기능**:
```python
from typing import Optional, Tuple, Dict, Any
from datetime import datetime, timedelta
import logging
from backend.services.supabase_service import SupabaseService

logger = logging.getLogger(__name__)

class DistributionService:
    """유사도 분포 계산 및 상대적 임계값 제공"""

    def __init__(self, supabase_service: SupabaseService):
        self.supabase = supabase_service
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_timestamp: Optional[datetime] = None
        self._memory_cache_ttl = timedelta(minutes=5)
        self._db_cache_ttl = timedelta(hours=24)

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
            logger.info("Recalculating similarity distribution...")
            result = await self.supabase.calculate_similarity_distribution()

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
        strategy: str = "p30_p60",
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
        calculated_at = datetime.fromisoformat(db_cache["calculated_at"])
        age = datetime.now() - calculated_at
        return age > self._db_cache_ttl

    async def _data_changed_significantly(
        self,
        db_cache: Dict[str, Any]
    ) -> bool:
        """데이터가 크게 변경되었는지 확인 (10% 임계값)"""
        current_count = await self.supabase.count_thought_units()
        cached_count = db_cache["thought_count"]

        if cached_count == 0:
            return True

        change_ratio = abs(current_count - cached_count) / cached_count
        return change_ratio > 0.1  # 10% 이상 변화
```

**캐싱 로직**:
1. 메모리 캐시 확인 (5분 TTL) → 히트 시 즉시 반환
2. DB 캐시 확인 → 유효성 검증 (24시간, 데이터 10% 변화)
3. 재계산 필요 시 RPC 호출

---

### Phase 2: SamplingStrategy 상대값 적용

#### 2.1 SamplingStrategy 리팩토링
**파일**: `backend/services/sampling.py` (수정)

**변경사항**:
```python
from backend.services.distribution_service import DistributionService

class SamplingStrategy:
    def __init__(
        self,
        distribution_service: DistributionService,  # 추가
        # low_range, mid_range, high_range 제거
        low_ratio: float = 0.4,
        mid_ratio: float = 0.35,
        high_ratio: float = 0.25
    ):
        self.dist_service = distribution_service
        self.low_ratio = low_ratio
        self.mid_ratio = mid_ratio
        self.high_ratio = high_ratio

    async def sample_initial(
        self,
        candidates: List[Dict[str, Any]],
        target_count: int = 100,
        strategy: str = "p10_p40",  # 기본값 변경
        custom_range: Optional[Tuple[int, int]] = None
    ) -> List[Dict[str, Any]]:
        """
        초기 샘플 선택 (3-tier 전략, 백분위수 기반)

        Process:
            1. DistributionService에서 전체 범위 백분위수 조회
               예: P10-P40 전략 → 전체 분포 조회

            2. 3-tier 구간 백분위수 직접 사용 (균등 분포 보장)
               Low:  P10-P20 (하위 10%, 가장 다른 아이디어)
               Mid:  P20-P30 (하위 10%, 중간 정도)
               High: P30-P40 (하위 10%, 약간 유사)

            3. 각 티어별 샘플링 수행
        """
        if not candidates:
            logger.warning("No candidates to sample")
            return []

        # 1. 전체 분포 조회
        dist = await self.dist_service.get_distribution()
        percentiles = dist["percentiles"]

        # 2. 3-tier 구간 백분위수 직접 사용 (strategy에 따라 동적 계산)
        if strategy == "p10_p40":
            low_range = (percentiles["p10"], percentiles["p20"])
            mid_range = (percentiles["p20"], percentiles["p30"])
            high_range = (percentiles["p30"], percentiles["p40"])
        elif strategy == "p30_p60":
            low_range = (percentiles["p30"], percentiles["p40"])
            mid_range = (percentiles["p40"], percentiles["p50"])
            high_range = (percentiles["p50"], percentiles["p60"])
        elif strategy == "p0_p30":
            low_range = (percentiles["p0"], percentiles["p10"])
            mid_range = (percentiles["p10"], percentiles["p20"])
            high_range = (percentiles["p20"], percentiles["p30"])
        elif strategy == "custom":
            if not custom_range:
                raise ValueError("custom_range required for custom strategy")
            min_pct, max_pct = custom_range
            # Custom은 3등분 방식 사용
            min_sim = percentiles[f"p{min_pct}"]
            max_sim = percentiles[f"p{max_pct}"]
            range_width = (max_sim - min_sim) / 3.0
            low_range = (min_sim, min_sim + range_width)
            mid_range = (min_sim + range_width, min_sim + 2 * range_width)
            high_range = (min_sim + 2 * range_width, max_sim)
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

        logger.info(
            f"Dynamic tier ranges: "
            f"Low={low_range}, Mid={mid_range}, High={high_range}"
        )

        # 3. 기존 로직 유지 (_filter_by_similarity, _diverse_sample)
        low_candidates = self._filter_by_similarity(candidates, low_range)
        mid_candidates = self._filter_by_similarity(candidates, mid_range)
        high_candidates = self._filter_by_similarity(candidates, high_range)

        # 4. 비율 기반 샘플링
        low_count = int(target_count * self.low_ratio)
        mid_count = int(target_count * self.mid_ratio)
        high_count = target_count - low_count - mid_count

        # 5. Fallback 로직 (상대적 임계값 기반)
        if not low_candidates and not mid_candidates and not high_candidates:
            logger.warning("No candidates in any tier, using full range")
            return self._diverse_sample(candidates, target_count)

        # 6. 각 티어별 샘플링
        low_sample = self._diverse_sample(low_candidates, low_count)
        mid_sample = self._diverse_sample(mid_candidates, mid_count)
        high_sample = self._diverse_sample(high_candidates, high_count)

        result = low_sample + mid_sample + high_sample

        logger.info(
            f"Sampled {len(result)} candidates: "
            f"Low={len(low_sample)}, Mid={len(mid_sample)}, High={len(high_sample)}"
        )

        return result
```

**핵심**: 절대값 하드코딩 제거, 동적 계산으로 전환

#### 2.2 API 엔드포인트 수정
**파일**: `backend/routers/pipeline.py` (수정)

**변경 엔드포인트**:
1. `/pipeline/collect-candidates`: `strategy` 파라미터 추가
2. `/pipeline/sample-initial`: `strategy` 파라미터 추가

**예시**:
```python
from backend.services.distribution_service import DistributionService

@router.post("/collect-candidates")
async def collect_candidates(
    strategy: str = Query(default="p10_p40", description="Percentile strategy: p10_p40, p30_p60, p0_p30, custom"),
    custom_min_pct: Optional[int] = Query(default=None, ge=0, le=100),
    custom_max_pct: Optional[int] = Query(default=None, ge=0, le=100),
    top_k: int = Query(default=20, ge=1, le=100),
    supabase_service: SupabaseService = Depends(get_supabase_service),
    dist_service: DistributionService = Depends(get_distribution_service),
):
    """
    후보 페어 수집 (상대적 임계값 기반)

    Args:
        strategy: 백분위수 전략
            - "p10_p40": 하위 10-40% 구간 (기본, 창의적 조합)
            - "p30_p60": 하위 30-60% 구간 (안전한 연결)
            - "p0_p30": 최하위 30% (매우 다른 아이디어)
            - "custom": custom_min_pct, custom_max_pct 사용
        custom_min_pct: 커스텀 최소 백분위수 (0-100)
        custom_max_pct: 커스텀 최대 백분위수 (0-100)
        top_k: 각 thought당 top-k 유사 페어 수

    Example:
        POST /pipeline/collect-candidates?strategy=p10_p40&top_k=20
        POST /pipeline/collect-candidates?strategy=custom&custom_min_pct=20&custom_max_pct=50
    """
    try:
        # 1. 상대적 임계값 계산
        min_similarity, max_similarity = await dist_service.get_relative_thresholds(
            strategy=strategy,
            custom_range=(custom_min_pct, custom_max_pct) if strategy == "custom" else None
        )

        logger.info(
            f"Collecting candidates with strategy={strategy}, "
            f"thresholds=[{min_similarity:.3f}, {max_similarity:.3f}], top_k={top_k}"
        )

        # 2. 기존 로직 유지 (find_candidate_pairs 호출)
        pairs = await supabase_service.find_candidate_pairs(
            min_similarity=min_similarity,
            max_similarity=max_similarity,
            top_k=top_k
        )

        # ... (나머지 로직 동일)

    except Exception as e:
        logger.error(f"Failed to collect candidates: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sample-initial")
async def sample_initial(
    target_count: int = Query(default=100, ge=10, le=1000),
    strategy: str = Query(default="p10_p40"),
    custom_min_pct: Optional[int] = Query(default=None),
    custom_max_pct: Optional[int] = Query(default=None),
    supabase_service: SupabaseService = Depends(get_supabase_service),
    dist_service: DistributionService = Depends(get_distribution_service),
):
    """
    초기 100개 샘플 평가

    Args:
        target_count: 샘플 개수 (기본 100)
        strategy: 샘플링 전략 (p10_p40, p30_p60, p0_p30, custom)
        custom_min_pct: 커스텀 최소 백분위수
        custom_max_pct: 커스텀 최대 백분위수
    """
    try:
        # 1. 미평가 후보 조회
        pending_candidates = await supabase_service.get_pending_candidates()

        if not pending_candidates:
            return {"message": "No pending candidates", "sampled": 0}

        # 2. SamplingStrategy로 샘플 선택
        sampling_strategy = SamplingStrategy(distribution_service=dist_service)

        samples = await sampling_strategy.sample_initial(
            candidates=pending_candidates,
            target_count=target_count,
            strategy=strategy,
            custom_range=(custom_min_pct, custom_max_pct) if strategy == "custom" else None
        )

        # ... (나머지 로직 동일)

    except Exception as e:
        logger.error(f"Failed to sample initial candidates: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

---

### Phase 3: 관리 엔드포인트 추가

#### 3.1 분포 조회 API
**파일**: `backend/routers/pipeline.py` (추가)

```python
@router.get("/distribution")
async def get_similarity_distribution(
    force_recalculate: bool = Query(default=False, description="Force recalculation"),
    dist_service: DistributionService = Depends(get_distribution_service),
):
    """
    유사도 분포 조회 및 관리

    Returns:
        {
            "thought_count": 1921,
            "total_pairs": 38420,
            "percentiles": {
                "p0": 0.26, "p10": 0.30, "p20": 0.32,
                "p30": 0.34, "p40": 0.36, "p50": 0.38,
                "p60": 0.40, "p70": 0.42, "p80": 0.44,
                "p90": 0.46, "p100": 0.50
            },
            "mean": 0.38,
            "stddev": 0.05,
            "calculated_at": "2026-01-26T10:00:00",
            "duration_ms": 5432,
            "strategies": {
                "p30_p60": [0.34, 0.40],
                "p10_p40": [0.30, 0.36],
                "p0_p30": [0.26, 0.34]
            }
        }

    Example:
        GET /pipeline/distribution  # 캐시 조회
        GET /pipeline/distribution?force_recalculate=true  # 강제 재계산
    """
    try:
        # 1. 분포 조회
        dist = await dist_service.get_distribution(force_recalculate=force_recalculate)

        # 2. 각 전략별 임계값 미리 계산 (프리뷰)
        strategies_preview = {}
        for strategy_name in ["p30_p60", "p10_p40", "p0_p30"]:
            min_sim, max_sim = await dist_service.get_relative_thresholds(
                strategy=strategy_name
            )
            strategies_preview[strategy_name] = [
                round(min_sim, 3),
                round(max_sim, 3)
            ]

        # 3. 결과 반환
        return {
            **dist,
            "strategies": strategies_preview
        }

    except Exception as e:
        logger.error(f"Failed to get distribution: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

**용도**:
- 디버깅: 현재 분포 확인
- 모니터링: 캐시 나이, 계산 시간
- 전략 선택: 각 전략의 실제 임계값 미리보기

---

## 5. 성능 분석

### 5.1 분포 계산 비용
```
데이터 규모: 1,921 thoughts
Top-K: 20
총 페어 수: 1,921 × 20 = 38,420

SQL 작업:
  1. CROSS JOIN (Top-K 필터) → O(n × k)
  2. PERCENTILE_CONT (11개 백분위수) → O(m log m)
  3. AVG, STDDEV → O(m)

예상 시간: 5-10초 (초기)

캐싱 전략:
  - 메모리 캐시: 5분 TTL (< 1ms)
  - DB 캐시: 24시간 TTL (~50ms)
  - 재계산: 24시간 경과 OR 데이터 10% 변화
```

### 5.2 쿼리 성능 비교
```
기존 (절대값):
  - find_candidate_pairs(0.05, 0.35) → 0개
  - Fallback(0.1, 0.4) → 0개
  - 총 시간: ~10초 (쿼리 2회 × 5초)

개선 (상대값):
  - get_distribution() → 캐시 히트 (< 1ms)
  - get_relative_thresholds() → 계산 (< 1ms)
  - find_candidate_pairs(0.32, 0.41) → 12,000개
  - 총 시간: ~5초 (쿼리 1회)

성능 개선: 50% 단축 + Fallback 제거
```

---

## 6. 배포 체크리스트

### 6.1 SQL 마이그레이션
- [ ] `008_create_similarity_distribution_cache.sql` 작성 및 실행
- [ ] `009_create_calculate_similarity_distribution.sql` 작성 및 실행
- [ ] RPC 함수 테스트: `SELECT calculate_similarity_distribution();`
- [ ] 초기 분포 계산 확인 (duration_ms < 15000)

### 6.2 Python 코드 배포
- [ ] `backend/services/distribution_service.py` 추가
- [ ] `backend/services/supabase_service.py` 메서드 추가
  - `get_similarity_distribution_cache()`
  - `calculate_similarity_distribution()`
  - `count_thought_units()`
- [ ] `backend/services/sampling.py` 리팩토링
- [ ] `backend/routers/pipeline.py` 엔드포인트 수정
- [ ] Dependency Injection 설정 (`get_distribution_service()`)

### 6.3 테스트
- [ ] Unit test: `test_distribution_service.py`
- [ ] Integration test: `test_pipeline_relative_thresholds.py`
- [ ] E2E test: 전체 파이프라인 (collect → sample → score)

### 6.4 모니터링
- [ ] 로그 확인: 분포 계산 시간, 캐시 히트/미스
- [ ] 메트릭 추가: duration_ms, cache_age_hours, cache_hit_rate

---

## 7. 예상 결과

### 7.1 성능 개선
| 지표 | 기존 | 개선 | 변화 |
|------|------|------|------|
| 후보 수집 성공률 | 0% (0개) | 100% (~12,000개) | ✅ |
| Fallback 실행 | 2회 | 0회 | -100% |
| 평균 응답 시간 | ~10초 | ~5초 | -50% |

### 7.2 사용자 경험
```
기존:
  1. collect-candidates → 0개
  2. Fallback → 0개
  3. 에러 ❌

개선:
  1. collect-candidates (p30_p60) → 12,000개
  2. sample-initial → 100개
  3. score-candidates → 45개
  4. 추천 성공 ✅
```

---

## 8. Critical Files for Implementation

구현에 가장 중요한 파일 5개:

1. **backend/services/sampling.py**
   - 이유: 3-tier 샘플링 로직 리팩토링 (절대값 → 상대값)
   - 변경: `__init__`, `sample_initial` 메서드 전면 수정

2. **backend/routers/pipeline.py**
   - 이유: API 엔드포인트 파라미터 변경
   - 변경: `/collect-candidates`, `/sample-initial`, `/distribution` (신규)

3. **backend/services/distribution_service.py** (신규 생성)
   - 이유: 분포 캐시 조회 및 상대적 임계값 계산 (핵심 로직)
   - 내용: `DistributionService` 클래스, 캐싱 전략

4. **backend/docs/supabase_migrations/008_create_similarity_distribution_cache.sql** (신규)
   - 이유: 분포 캐시 테이블 스키마 정의
   - 내용: `similarity_distribution_cache` 테이블, 백분위수 저장

5. **backend/docs/supabase_migrations/009_create_calculate_similarity_distribution.sql** (신규)
   - 이유: 분포 계산 RPC 함수 (핵심 로직)
   - 내용: `calculate_similarity_distribution()` PostgreSQL 함수 (Top-K 샘플링, PERCENTILE_CONT)

---

## 9. 구현 순서

### Step 1: SQL 인프라 구축
1. `008_create_similarity_distribution_cache.sql` 작성
2. `009_create_calculate_similarity_distribution.sql` 작성
3. Supabase에서 실행 및 테스트

### Step 2: Python 서비스 구현
1. `distribution_service.py` 작성
2. `supabase_service.py`에 헬퍼 메서드 추가
3. Unit test 작성 및 실행

### Step 3: 샘플링 로직 리팩토링
1. `sampling.py` 수정 (상대값 적용)
2. `pipeline.py` 엔드포인트 수정
3. Integration test 작성 및 실행

### Step 4: 검증 및 배포
1. 전체 파이프라인 E2E 테스트
2. 성능 모니터링 설정
3. 프로덕션 배포

---

**예상 구현 시간**: 2-3시간 (테스트 포함)
