# HNSW 인덱스 + Top-K 알고리즘 구현 계획

## 목표

Step 3 (select-pairs) 성능 최적화:
- **현재**: 60초+ 타임아웃 (O(n²) = 1.15M 조합)
- **목표**: 5초 이하 (Top-K 방식)

## 구현 순서

### Phase 1: HNSW 인덱스 생성 (30분)

**파일**: `backend/docs/supabase_migrations/004_create_hnsw_index.sql`

```sql
-- 기존 IVFFlat 인덱스 제거 (주석처리되어 있음)
DROP INDEX IF EXISTS idx_thought_units_embedding;

-- HNSW 인덱스 생성
CREATE INDEX idx_thought_units_embedding_hnsw
ON thought_units
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 200);

-- 통계 업데이트
ANALYZE thought_units;
```

**배포 방법**:
1. Supabase Dashboard → SQL Editor
2. 위 SQL 실행
3. 완료 확인: `\d thought_units` (인덱스 목록)

**예상 효과**:
- 인덱스 생성 시간: ~30초 (1,521개 벡터)
- 인덱스 크기: +3-7MB
- 쿼리 성능: 60초+ → 40-70ms (Top-K 쿼리 시)

---

### Phase 2: Top-K 알고리즘 구현 (1.5시간)

#### Step 2.1: RPC 함수 수정

**파일**: `backend/docs/supabase_setup.sql` (Line 89-118)

**현재 구조**:
```sql
-- find_similar_pairs() 함수
-- O(n²) 조인 → BETWEEN 필터
```

**새로운 구조**:
```sql
CREATE OR REPLACE FUNCTION find_similar_pairs_topk(
    min_sim FLOAT DEFAULT 0.05,
    max_sim FLOAT DEFAULT 0.35,
    top_k INT DEFAULT 30,  -- ← NEW: 각 thought당 상위 K개
    lim INT DEFAULT 20
)
RETURNS TABLE (
    thought_a_id INTEGER,
    thought_b_id INTEGER,
    similarity_score FLOAT
) AS $$
BEGIN
    RETURN QUERY
    WITH ranked_pairs AS (
        SELECT
            a.id AS thought_a_id,
            b.id AS thought_b_id,
            (1 - (a.embedding <=> b.embedding))::FLOAT AS similarity_score,
            ROW_NUMBER() OVER (
                PARTITION BY a.id
                ORDER BY (a.embedding <=> b.embedding) ASC  -- 거리 오름차순 = 유사도 높은 순
            ) AS rank
        FROM thought_units a
        CROSS JOIN LATERAL (
            SELECT id, embedding
            FROM thought_units
            WHERE id != a.id  -- 자기 자신 제외
              AND raw_note_id != a.raw_note_id  -- 동일 출처 제외
            ORDER BY embedding <=> a.embedding  -- ← HNSW 인덱스 활용
            LIMIT top_k  -- ← 각 a마다 상위 K개만
        ) b
        WHERE (1 - (a.embedding <=> b.embedding)) BETWEEN min_sim AND max_sim
    )
    SELECT
        LEAST(thought_a_id, thought_b_id) AS thought_a_id,
        GREATEST(thought_a_id, thought_b_id) AS thought_b_id,
        similarity_score
    FROM ranked_pairs
    WHERE rank <= top_k
    ORDER BY similarity_score DESC
    LIMIT lim;
END;
$$ LANGUAGE plpgsql;
```

**핵심 변경점**:
1. `CROSS JOIN LATERAL` 사용 → 각 thought마다 Top-K 검색
2. `ORDER BY embedding <=> a.embedding` → HNSW 인덱스 자동 활용
3. `LIMIT top_k` → O(n²) → O(n × K) 복잡도 감소
4. `ROW_NUMBER()` → 중복 제거 및 순위 매기기

**복잡도 비교**:
- 기존: O(n²) = 1,521 × 1,521 = 2.3M 비교
- 개선: O(n × K) = 1,521 × 30 = 45,630 비교 (98% 감소)

---

#### Step 2.2: Python 서비스 수정

**파일**: `backend/services/supabase_service.py` (Line 319-369)

**수정 전**:
```python
async def find_candidate_pairs(
    self,
    min_similarity: float = 0.05,
    max_similarity: float = 0.35,
    limit: int = 20
) -> List[dict]:
    response = await self.client.rpc(
        "find_similar_pairs",  # ← 기존 함수
        {
            "min_sim": min_similarity,
            "max_sim": max_similarity,
            "lim": limit
        }
    ).execute()
```

**수정 후**:
```python
async def find_candidate_pairs(
    self,
    min_similarity: float = 0.05,
    max_similarity: float = 0.35,
    top_k: int = 30,  # ← NEW
    limit: int = 20
) -> List[dict]:
    """
    Top-K 알고리즘으로 후보 페어 검색

    Args:
        min_similarity: 최소 유사도 (0.05)
        max_similarity: 최대 유사도 (0.35)
        top_k: 각 thought당 검색할 상위 K개 (30)
        limit: 최종 반환할 페어 수 (20)

    Returns:
        similarity_score 내림차순 정렬된 페어 목록

    Performance:
        - 기존: O(n²) = 60초+ 타임아웃
        - 개선: O(n × K) = 5초 이하
    """
    await self._ensure_initialized()

    try:
        response = await self.client.rpc(
            "find_similar_pairs_topk",  # ← 새 함수
            {
                "min_sim": min_similarity,
                "max_sim": max_similarity,
                "top_k": top_k,
                "lim": limit
            }
        ).execute()

        if not response.data:
            logger.warning("No candidate pairs found")
            return []

        logger.info(f"Found {len(response.data)} candidate pairs (top_k={top_k})")
        return response.data

    except Exception as e:
        logger.error(f"Failed to find candidate pairs: {e}")
        raise
```

---

#### Step 2.3: API 엔드포인트 수정

**파일**: `backend/routers/pipeline.py` (Line 556-640)

**수정 전**:
```python
@router.post("/select-pairs")
async def select_pairs(
    min_similarity: float = Query(default=0.05, ...),
    max_similarity: float = Query(default=0.35, ...),
    min_score: int = Query(default=65, ...),
    top_n: int = Query(default=5, ...),
    ...
):
```

**수정 후**:
```python
@router.post("/select-pairs")
async def select_pairs(
    min_similarity: float = Query(default=0.05, ge=0.0, le=1.0),
    max_similarity: float = Query(default=0.35, ge=0.0, le=1.0),
    top_k: int = Query(default=30, ge=10, le=100, description="각 thought당 검색할 상위 K개"),  # ← NEW
    min_score: int = Query(default=65, ge=0, le=100),
    top_n: int = Query(default=5, ge=1, le=20),
    ...
):
    """
    Step 3: 사고 단위 페어 선택 (Top-K 알고리즘)

    Query Parameters:
        top_k: 각 thought마다 유사한 상위 K개 검색 (기본 30)
               - 클수록: 다양한 조합, 느린 속도
               - 작을수록: 빠른 속도, 제한된 조합
    """
    logger.info(f"=== Step 3: Select Pairs (Top-K={top_k}) ===")

    # 1. Top-K 후보 검색 (HNSW + LATERAL JOIN)
    candidates = await supabase_service.find_candidate_pairs(
        min_similarity=min_similarity,
        max_similarity=max_similarity,
        top_k=top_k,  # ← NEW
        limit=100  # Claude API로 보낼 최대 후보 수
    )
```

---

### Phase 3: 테스트 및 검증 (1시간)

#### Test 1: 기본 동작 확인
```bash
# 1. HNSW 인덱스 확인
curl http://localhost:8000/health

# 2. Top-K=30으로 실행
curl -X POST "http://localhost:8000/pipeline/select-pairs?top_k=30&min_score=65&top_n=5"

# 예상 응답 시간: < 10초
```

**성공 기준**:
- ✅ 타임아웃 없이 완료
- ✅ 5개 thought_pairs 생성
- ✅ similarity_score가 0.05~0.35 범위
- ✅ 로그에 "Found N candidate pairs (top_k=30)" 출력

#### Test 2: Top-K 파라미터 테스트
```python
# backend/tests/integration/test_topk_performance.py
import pytest
import time

@pytest.mark.asyncio
async def test_topk_performance():
    """Top-K 값에 따른 성능 측정"""
    test_cases = [
        {"top_k": 10, "expected_time": 3},   # 15K 비교
        {"top_k": 30, "expected_time": 5},   # 45K 비교
        {"top_k": 50, "expected_time": 8},   # 76K 비교
    ]

    for case in test_cases:
        start = time.time()
        response = await client.post(
            "/pipeline/select-pairs",
            params={"top_k": case["top_k"], "top_n": 5}
        )
        elapsed = time.time() - start

        assert response.status_code == 200
        assert elapsed < case["expected_time"]
        print(f"✅ top_k={case['top_k']}: {elapsed:.2f}s")

@pytest.mark.asyncio
async def test_topk_quality():
    """Top-K 결과 품질 검증"""
    # top_k=10 vs top_k=50 비교
    # 더 큰 K가 더 다양한 조합을 찾는지 확인

    result_10 = await client.post("/pipeline/select-pairs", params={"top_k": 10})
    result_50 = await client.post("/pipeline/select-pairs", params={"top_k": 50})

    pairs_10 = set((p["thought_a_id"], p["thought_b_id"]) for p in result_10.json()["pairs"])
    pairs_50 = set((p["thought_a_id"], p["thought_b_id"]) for p in result_50.json()["pairs"])

    # top_k=50이 더 많은 조합을 포함해야 함
    assert len(pairs_50) >= len(pairs_10)
    print(f"✅ top_k=10: {len(pairs_10)} pairs, top_k=50: {len(pairs_50)} pairs")
```

#### Test 3: HNSW 인덱스 활용 확인
```sql
-- Supabase SQL Editor에서 실행
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM find_similar_pairs_topk(0.05, 0.35, 30, 20);

-- 확인 항목:
-- 1. Index Scan using idx_thought_units_embedding_hnsw (✅)
-- 2. Execution Time < 1000ms (✅)
-- 3. Buffers: shared hit >> shared read (캐시 활용)
```

---

### Phase 4: 문서 업데이트 (30분)

#### 1. CLAUDE.md 업데이트
```markdown
## Step 3: Select Pairs (Top-K Algorithm)

**알고리즘**: HNSW 인덱스 + LATERAL JOIN Top-K 검색

**성능**:
- 복잡도: O(n × K) (기존 O(n²)에서 98% 개선)
- 실행 시간: ~5초 (기존 60초+ 타임아웃)
- 인덱스: HNSW (m=16, ef_construction=200)

**파라미터**:
- `top_k`: 각 thought당 검색할 상위 K개 (기본 30)
  - 권장: 20-50 (1,521개 기준)
  - 10K 벡터 도달 시: 50-100으로 증가

**쿼리 구조**:
```sql
CROSS JOIN LATERAL (
    SELECT * FROM thought_units
    WHERE id != a.id
    ORDER BY embedding <=> a.embedding  -- HNSW 활용
    LIMIT top_k
)
```
```

#### 2. supabase_setup.sql 업데이트
```sql
-- Line 36-37 수정
-- HNSW 인덱스로 교체 (IVFFlat 제거)
CREATE INDEX idx_thought_units_embedding_hnsw ON thought_units
USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 200);
COMMENT ON INDEX idx_thought_units_embedding_hnsw IS 'HNSW 벡터 인덱스 (Top-K 검색 최적화)';
```

---

## 예상 성능 개선

| 항목 | 기존 (Range Query) | 개선 (Top-K) | 개선율 |
|------|-------------------|-------------|--------|
| **알고리즘** | O(n²) 전체 조합 | O(n × K) Top-K | 98% |
| **비교 횟수** | 2.3M | 45,630 (K=30) | 98% |
| **실행 시간** | 60초+ (타임아웃) | 5초 이하 | 92% |
| **인덱스** | None (주석처리) | HNSW | - |
| **메모리** | - | +3-7MB | +0.1% |

### 1,521개 벡터 기준 (현재)
- Top-K=10: 15,210 비교 → ~3초
- Top-K=30: 45,630 비교 → ~5초
- Top-K=50: 76,050 비교 → ~8초

### 10,000개 벡터 도달 시
- Top-K=30: 300,000 비교 → ~15초
- Top-K=50: 500,000 비교 → ~25초
- Top-K=100: 1,000,000 비교 → ~50초

**권장**: 벡터 수에 따라 top_k 동적 조정
```python
# config.py
TOP_K = max(20, min(100, int(total_thoughts * 0.02)))  # 전체의 2%
```

---

## 구현 체크리스트

### Phase 1: HNSW 인덱스
- [ ] SQL 마이그레이션 파일 작성 (004_create_hnsw_index.sql)
- [ ] Supabase에서 인덱스 생성 실행
- [ ] `\d thought_units` 로 인덱스 확인
- [ ] ANALYZE thought_units 실행

### Phase 2: Top-K 알고리즘
- [ ] find_similar_pairs_topk() RPC 함수 작성
- [ ] Supabase에서 함수 배포
- [ ] supabase_service.py 수정 (top_k 파라미터 추가)
- [ ] pipeline.py 엔드포인트 수정 (top_k Query 추가)

### Phase 3: 테스트
- [ ] 기본 동작 확인 (curl 요청)
- [ ] Top-K 파라미터 테스트 (10, 30, 50)
- [ ] EXPLAIN ANALYZE로 HNSW 활용 확인
- [ ] 통합 테스트 작성 및 실행

### Phase 4: 문서화
- [ ] CLAUDE.md 업데이트 (알고리즘 설명)
- [ ] supabase_setup.sql 주석 업데이트
- [ ] README.md 성능 벤치마크 추가

---

## 롤백 플랜

**긴급 롤백 필요 시**:
1. 기존 Range Query 함수 유지 (find_similar_pairs)
2. API에서 top_k=None이면 기존 로직 사용
3. HNSW 인덱스는 유지 (Range Query도 개선 효과)

```python
# 호환성 유지
if top_k is not None:
    response = await self.client.rpc("find_similar_pairs_topk", ...)
else:
    response = await self.client.rpc("find_similar_pairs", ...)  # 기존
```

---

## 다음 단계

1. **Phase 1 구현** (30분) → HNSW 인덱스 생성
2. **Phase 2.1 구현** (30분) → RPC 함수 작성
3. **Phase 2.2-2.3 구현** (1시간) → Python 코드 수정
4. **Phase 3 테스트** (1시간) → 성능 및 품질 검증
5. **Phase 4 문서화** (30분) → CLAUDE.md 업데이트

**총 예상 시간**: 3.5시간
