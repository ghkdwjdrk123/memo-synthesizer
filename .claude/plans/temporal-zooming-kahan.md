# 샘플링 기반 후보 마이닝 + 전역 분포 스케치 구현 계획

## 개요

기존 Distance Table 방식(전쌍 계산)을 폐기하고, 두 축으로 재설계:

| 축 | 목적 | RPC |
|----|------|-----|
| **(A) Candidate Mining** | src당 10-20개 후보 생성 | `mine_candidate_pairs()` |
| **(B) Global Distribution Sketch** | 전역 분포 근사 (p0-p100) | `build_distribution_sketch()` + `calculate_distribution_from_sketch()` |

### 핵심 제약
- Supabase 60초 statement timeout
- OFFSET 금지, ORDER BY random() 금지
- 같은 memo_id 페어 제외 필수
- 전쌍 계산/정렬 금지

---

## Phase 1: DDL 설계

### 1.1 thought_units에 rand_key 추가

**파일**: `backend/docs/supabase_migrations/015_add_rand_key.sql`

```sql
-- rand_key: 빠른 결정론적 샘플링용
ALTER TABLE thought_units
ADD COLUMN IF NOT EXISTS rand_key DOUBLE PRECISION DEFAULT random();

UPDATE thought_units SET rand_key = random() WHERE rand_key IS NULL;
ALTER TABLE thought_units ALTER COLUMN rand_key SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_thought_units_rand_key
ON thought_units (rand_key);

COMMENT ON COLUMN thought_units.rand_key IS
'결정론적 샘플링용. seed 기반 범위 조회로 ORDER BY random() 대체';
```

**rand_key vs TABLESAMPLE 비교**:
| 방식 | 장점 | 단점 |
|------|------|------|
| rand_key | 결정론적, 재현 가능, 인덱스 활용 | 컬럼 추가 필요 |
| TABLESAMPLE | 컬럼 불필요 | 비결정론적, seed 재현 어려움 |

**선택**: rand_key (재현 가능성 + 인덱스 활용)

---

### 1.2 pair_mining_progress 테이블

**파일**: `backend/docs/supabase_migrations/016_create_mining_progress.sql`

```sql
CREATE TABLE IF NOT EXISTS pair_mining_progress (
    id SERIAL PRIMARY KEY,
    run_id UUID DEFAULT gen_random_uuid(),
    last_src_id INTEGER NOT NULL DEFAULT 0,
    total_src_processed INTEGER NOT NULL DEFAULT 0,
    total_pairs_inserted BIGINT NOT NULL DEFAULT 0,
    avg_candidates_per_src FLOAT,

    -- 파라미터 스냅샷
    src_batch INTEGER NOT NULL DEFAULT 30,
    dst_sample INTEGER NOT NULL DEFAULT 1200,
    k_per_src INTEGER NOT NULL DEFAULT 15,
    p_lo FLOAT NOT NULL DEFAULT 0.10,
    p_hi FLOAT NOT NULL DEFAULT 0.35,
    max_rounds INTEGER NOT NULL DEFAULT 3,
    seed INTEGER NOT NULL DEFAULT 42,

    -- 상태
    status TEXT NOT NULL DEFAULT 'in_progress'
        CHECK (status IN ('pending', 'in_progress', 'completed', 'paused', 'failed')),
    started_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    error_message TEXT
);

CREATE UNIQUE INDEX idx_pmp_active ON pair_mining_progress ((1))
WHERE status = 'in_progress';
```

---

### 1.3 similarity_samples 테이블 (전역 분포 스케치용)

**선택**: Raw Sample 저장 방식 (안1)

**이유**:
- Histogram/TDigest는 PostgreSQL 네이티브 지원 없음 (확장 필요)
- Raw sample은 PERCENTILE_CONT로 직접 계산 가능
- 샘플 수 제한(10만개)으로 저장 공간 관리 가능

**파일**: `backend/docs/supabase_migrations/017_create_similarity_samples.sql`

```sql
CREATE TABLE IF NOT EXISTS similarity_samples (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL,
    similarity FLOAT NOT NULL CHECK (similarity >= 0 AND similarity <= 1),
    src_id INTEGER,  -- 선택적 (디버깅용)
    dst_id INTEGER,  -- 선택적 (디버깅용)
    seed INTEGER,
    policy TEXT DEFAULT 'random_pairs',  -- 샘플링 정책
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 최신 run의 샘플만 빠르게 조회
CREATE INDEX idx_ss_run_id ON similarity_samples (run_id);
CREATE INDEX idx_ss_created_at ON similarity_samples (created_at DESC);

-- 오래된 샘플 정리용
CREATE INDEX idx_ss_cleanup ON similarity_samples (created_at)
WHERE created_at < NOW() - INTERVAL '7 days';

COMMENT ON TABLE similarity_samples IS
'전역 분포 스케치용 샘플. 10만개 제한 권장. p0-p100 계산에 사용.';
```

---

### 1.4 기존 pair_candidates 테이블 활용 ✅

기존 테이블 그대로 사용:
- `llm_score`, `llm_status`, `llm_attempts` 재활용
- 마이닝 결과를 직접 INSERT
- 추가 컬럼 불필요 (band_lo/band_hi는 로그로만 기록)

---

## Phase 2: RPC 함수 설계

### 2.1 mine_candidate_pairs() - 후보 마이닝

**파일**: `backend/docs/supabase_migrations/018_mine_candidate_pairs_rpc.sql`

```sql
CREATE OR REPLACE FUNCTION mine_candidate_pairs(
    p_last_src_id INTEGER DEFAULT 0,
    p_src_batch INTEGER DEFAULT 30,
    p_dst_sample INTEGER DEFAULT 1200,
    p_k INTEGER DEFAULT 15,
    p_lo FLOAT DEFAULT 0.10,
    p_hi FLOAT DEFAULT 0.35,
    p_seed INTEGER DEFAULT 42,
    p_max_rounds INTEGER DEFAULT 3
)
RETURNS jsonb AS $$
DECLARE
    v_src_ids INTEGER[];
    v_src_count INTEGER;
    v_new_last_src_id INTEGER;
    v_inserted_count BIGINT := 0;
    v_round INTEGER := 1;
    v_current_seed INTEGER;
    v_start_time TIMESTAMPTZ;
    v_band_lo FLOAT;
    v_band_hi FLOAT;
BEGIN
    v_start_time := clock_timestamp();
    v_current_seed := p_seed;

    -- 1. 키셋 페이징으로 src 배치 선택 (OFFSET 금지)
    SELECT ARRAY_AGG(id ORDER BY id)
    INTO v_src_ids
    FROM (
        SELECT id FROM thought_units
        WHERE id > p_last_src_id
          AND embedding IS NOT NULL
        ORDER BY id
        LIMIT p_src_batch
    ) t;

    v_src_count := COALESCE(array_length(v_src_ids, 1), 0);

    IF v_src_count = 0 THEN
        RETURN jsonb_build_object(
            'success', true,
            'new_last_src_id', p_last_src_id,
            'inserted_count', 0,
            'src_processed_count', 0,
            'message', 'No more sources to process'
        );
    END IF;

    v_new_last_src_id := v_src_ids[array_length(v_src_ids, 1)];

    -- 2. 라운드 반복 (후보 부족 시 seed 변경하여 재시도)
    WHILE v_round <= p_max_rounds LOOP
        -- 2.1 rand_key 기반 dst 샘플링 + 유사도 계산 + 분위수 계산
        WITH dst_sample AS (
            -- seed 기반 결정론적 샘플링
            SELECT id, embedding, raw_note_id
            FROM thought_units
            WHERE embedding IS NOT NULL
              AND rand_key >= (v_current_seed::FLOAT % 1000000) / 1000000.0
            ORDER BY rand_key
            LIMIT p_dst_sample
        ),
        similarity_calc AS (
            SELECT
                src.id AS src_id,
                dst.id AS dst_id,
                src.raw_note_id AS src_memo,
                dst.raw_note_id AS dst_memo,
                (1 - (src.embedding <=> dst.embedding))::FLOAT AS similarity
            FROM thought_units src
            CROSS JOIN dst_sample dst
            WHERE src.id = ANY(v_src_ids)
              AND src.id != dst.id
              AND src.raw_note_id != dst.raw_note_id  -- 같은 메모 제외
        ),
        -- 배치 내 분위수 계산
        band_calc AS (
            SELECT
                PERCENTILE_CONT(p_lo) WITHIN GROUP (ORDER BY similarity) AS band_lo,
                PERCENTILE_CONT(p_hi) WITHIN GROUP (ORDER BY similarity) AS band_hi
            FROM similarity_calc
        ),
        -- 밴드 내 후보 선택 (src당 k개)
        ranked_candidates AS (
            SELECT
                LEAST(sc.src_id, sc.dst_id) AS thought_a_id,
                GREATEST(sc.src_id, sc.dst_id) AS thought_b_id,
                sc.similarity,
                sc.src_memo AS raw_note_id_a,
                sc.dst_memo AS raw_note_id_b,
                ROW_NUMBER() OVER (PARTITION BY sc.src_id ORDER BY sc.similarity) AS rn
            FROM similarity_calc sc, band_calc bc
            WHERE sc.similarity BETWEEN bc.band_lo AND bc.band_hi
        ),
        inserted AS (
            INSERT INTO pair_candidates
                (thought_a_id, thought_b_id, similarity, raw_note_id_a, raw_note_id_b)
            SELECT thought_a_id, thought_b_id, similarity, raw_note_id_a, raw_note_id_b
            FROM ranked_candidates
            WHERE rn <= p_k
            ON CONFLICT (thought_a_id, thought_b_id) DO NOTHING
            RETURNING 1
        )
        SELECT COUNT(*), bc.band_lo, bc.band_hi
        INTO v_inserted_count, v_band_lo, v_band_hi
        FROM inserted, band_calc bc
        GROUP BY bc.band_lo, bc.band_hi;

        -- 50% 이상 생성되면 충분
        IF v_inserted_count >= (v_src_count * p_k * 0.5) THEN
            EXIT;
        END IF;

        -- seed 변경 (황금비 기반)
        v_current_seed := v_current_seed + 618033;
        v_round := v_round + 1;
    END LOOP;

    RETURN jsonb_build_object(
        'success', true,
        'new_last_src_id', v_new_last_src_id,
        'inserted_count', COALESCE(v_inserted_count, 0),
        'src_processed_count', v_src_count,
        'rounds_used', v_round,
        'band_lo', v_band_lo,
        'band_hi', v_band_hi,
        'avg_candidates_per_src', COALESCE(v_inserted_count::FLOAT / v_src_count, 0),
        'duration_ms', EXTRACT(EPOCH FROM (clock_timestamp() - v_start_time)) * 1000
    );

EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', SQLERRM,
            'last_src_id', p_last_src_id
        );
END;
$$ LANGUAGE plpgsql;
```

**파라미터 권장값**:
| 파라미터 | 기본값 | 범위 | 설명 |
|---------|--------|------|------|
| src_batch | 30 | 20-40 | 배치당 src 수 |
| dst_sample | 1200 | 800-1500 | dst 샘플 크기 |
| k | 15 | 10-20 | src당 후보 수 |
| p_lo | 0.10 | 0.05-0.15 | 하위 분위수 |
| p_hi | 0.35 | 0.25-0.45 | 상위 분위수 |
| max_rounds | 3 | 2-5 | 최대 재시도 |

---

### 2.2 build_distribution_sketch() - 전역 분포 샘플 수집

**파일**: `backend/docs/supabase_migrations/019_build_distribution_sketch_rpc.sql`

```sql
CREATE OR REPLACE FUNCTION build_distribution_sketch(
    p_seed INTEGER DEFAULT 42,
    p_src_sample INTEGER DEFAULT 200,
    p_dst_sample INTEGER DEFAULT 500,
    p_rounds INTEGER DEFAULT 1,
    p_exclude_same_memo BOOLEAN DEFAULT TRUE,
    p_policy TEXT DEFAULT 'random_pairs'
)
RETURNS jsonb AS $$
DECLARE
    v_run_id UUID;
    v_inserted_count BIGINT := 0;
    v_round INTEGER := 1;
    v_current_seed INTEGER;
    v_start_time TIMESTAMPTZ;
    v_total_thoughts INTEGER;
BEGIN
    v_start_time := clock_timestamp();
    v_run_id := gen_random_uuid();
    v_current_seed := p_seed;

    -- 전체 thought 수 확인
    SELECT COUNT(*) INTO v_total_thoughts
    FROM thought_units WHERE embedding IS NOT NULL;

    -- 라운드 반복
    WHILE v_round <= p_rounds LOOP
        WITH src_sample AS (
            -- 랜덤 src 샘플
            SELECT id, embedding, raw_note_id
            FROM thought_units
            WHERE embedding IS NOT NULL
              AND rand_key >= (v_current_seed::FLOAT % 1000000) / 1000000.0
            ORDER BY rand_key
            LIMIT p_src_sample
        ),
        dst_sample AS (
            -- 랜덤 dst 샘플 (다른 seed)
            SELECT id, embedding, raw_note_id
            FROM thought_units
            WHERE embedding IS NOT NULL
              AND rand_key >= ((v_current_seed + 500000)::FLOAT % 1000000) / 1000000.0
            ORDER BY rand_key
            LIMIT p_dst_sample
        ),
        similarity_calc AS (
            SELECT
                src.id AS src_id,
                dst.id AS dst_id,
                (1 - (src.embedding <=> dst.embedding))::FLOAT AS similarity
            FROM src_sample src
            CROSS JOIN dst_sample dst
            WHERE src.id != dst.id
              AND (NOT p_exclude_same_memo OR src.raw_note_id != dst.raw_note_id)
        ),
        inserted AS (
            INSERT INTO similarity_samples (run_id, similarity, src_id, dst_id, seed, policy)
            SELECT v_run_id, similarity, src_id, dst_id, v_current_seed, p_policy
            FROM similarity_calc
            RETURNING 1
        )
        SELECT COUNT(*) INTO v_inserted_count FROM inserted;

        v_current_seed := v_current_seed + 618033;
        v_round := v_round + 1;
    END LOOP;

    RETURN jsonb_build_object(
        'success', true,
        'run_id', v_run_id,
        'inserted_samples', v_inserted_count,
        'total_thoughts', v_total_thoughts,
        'coverage_estimate', ROUND((p_src_sample * p_dst_sample * p_rounds)::FLOAT /
                                    (v_total_thoughts * v_total_thoughts) * 100, 4),
        'duration_ms', EXTRACT(EPOCH FROM (clock_timestamp() - v_start_time)) * 1000
    );

EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object('success', false, 'error', SQLERRM);
END;
$$ LANGUAGE plpgsql;
```

**같은 memo_id 제외 판단**:
- 전역 분포: **제외 권장** (`p_exclude_same_memo=TRUE`)
- 이유: 같은 메모 내 thought는 유사도가 높아 분포 왜곡 가능

---

### 2.3 calculate_distribution_from_sketch() - 분포 계산

**파일**: `backend/docs/supabase_migrations/020_calculate_distribution_from_sketch_rpc.sql`

```sql
CREATE OR REPLACE FUNCTION calculate_distribution_from_sketch(
    p_run_id UUID DEFAULT NULL,  -- NULL이면 최신 사용
    p_sample_limit INTEGER DEFAULT 100000
)
RETURNS jsonb AS $$
DECLARE
    v_run_id UUID;
    v_sample_count BIGINT;
    v_result JSONB;
    v_start_time TIMESTAMPTZ;
BEGIN
    v_start_time := clock_timestamp();

    -- 최신 run_id 찾기
    IF p_run_id IS NULL THEN
        SELECT run_id INTO v_run_id
        FROM similarity_samples
        ORDER BY created_at DESC
        LIMIT 1;
    ELSE
        v_run_id := p_run_id;
    END IF;

    IF v_run_id IS NULL THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'No samples found. Run build_distribution_sketch() first.'
        );
    END IF;

    -- 샘플 수 확인
    SELECT COUNT(*) INTO v_sample_count
    FROM similarity_samples
    WHERE run_id = v_run_id;

    -- 백분위수 계산
    SELECT jsonb_build_object(
        'sample_count', v_sample_count,
        'run_id', v_run_id,
        'p0', MIN(similarity),
        'p10', PERCENTILE_CONT(0.1) WITHIN GROUP (ORDER BY similarity),
        'p20', PERCENTILE_CONT(0.2) WITHIN GROUP (ORDER BY similarity),
        'p30', PERCENTILE_CONT(0.3) WITHIN GROUP (ORDER BY similarity),
        'p40', PERCENTILE_CONT(0.4) WITHIN GROUP (ORDER BY similarity),
        'p50', PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY similarity),
        'p60', PERCENTILE_CONT(0.6) WITHIN GROUP (ORDER BY similarity),
        'p70', PERCENTILE_CONT(0.7) WITHIN GROUP (ORDER BY similarity),
        'p80', PERCENTILE_CONT(0.8) WITHIN GROUP (ORDER BY similarity),
        'p90', PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY similarity),
        'p100', MAX(similarity),
        'mean', AVG(similarity),
        'stddev', STDDEV(similarity)
    ) INTO v_result
    FROM (
        SELECT similarity
        FROM similarity_samples
        WHERE run_id = v_run_id
        ORDER BY RANDOM()
        LIMIT p_sample_limit
    ) sampled;

    -- similarity_distribution_cache 갱신 (기존 형식 유지)
    INSERT INTO similarity_distribution_cache (
        id, thought_unit_count, total_pair_count,
        p0, p10, p20, p30, p40, p50, p60, p70, p80, p90, p100,
        mean, stddev, calculated_at, embedding_model, calculation_duration_ms
    ) VALUES (
        1,
        (SELECT COUNT(*) FROM thought_units WHERE embedding IS NOT NULL),
        v_sample_count,  -- 샘플 수 (전쌍 아님)
        (v_result->>'p0')::FLOAT,
        (v_result->>'p10')::FLOAT,
        (v_result->>'p20')::FLOAT,
        (v_result->>'p30')::FLOAT,
        (v_result->>'p40')::FLOAT,
        (v_result->>'p50')::FLOAT,
        (v_result->>'p60')::FLOAT,
        (v_result->>'p70')::FLOAT,
        (v_result->>'p80')::FLOAT,
        (v_result->>'p90')::FLOAT,
        (v_result->>'p100')::FLOAT,
        (v_result->>'mean')::FLOAT,
        (v_result->>'stddev')::FLOAT,
        NOW(),
        'text-embedding-3-small',
        EXTRACT(EPOCH FROM (clock_timestamp() - v_start_time)) * 1000
    )
    ON CONFLICT (id) DO UPDATE SET
        thought_unit_count = EXCLUDED.thought_unit_count,
        total_pair_count = EXCLUDED.total_pair_count,
        p0 = EXCLUDED.p0, p10 = EXCLUDED.p10, p20 = EXCLUDED.p20,
        p30 = EXCLUDED.p30, p40 = EXCLUDED.p40, p50 = EXCLUDED.p50,
        p60 = EXCLUDED.p60, p70 = EXCLUDED.p70, p80 = EXCLUDED.p80,
        p90 = EXCLUDED.p90, p100 = EXCLUDED.p100,
        mean = EXCLUDED.mean, stddev = EXCLUDED.stddev,
        calculated_at = EXCLUDED.calculated_at,
        calculation_duration_ms = EXCLUDED.calculation_duration_ms;

    RETURN jsonb_build_object(
        'success', true,
        'distribution', v_result,
        'cached', true,
        'is_approximate', true,  -- 명시적으로 근사값임을 표시
        'duration_ms', EXTRACT(EPOCH FROM (clock_timestamp() - v_start_time)) * 1000
    );

EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object('success', false, 'error', SQLERRM);
END;
$$ LANGUAGE plpgsql;
```

---

## Phase 3: Python 서비스 변경

### 3.1 CandidateMiningService (신규)

**파일**: `backend/services/candidate_mining_service.py`

```python
class CandidateMiningService:
    async def mine_batch(self, last_src_id: int = 0, **params) -> dict
    async def mine_full_background(self, **params) -> dict
    async def get_progress(self) -> dict
    async def resume_mining(self) -> dict
```

### 3.2 distribution_service.py 수정

**변경 사항**:
1. `calculate_distribution_from_distance_table()` 호출 → `calculate_distribution_from_sketch()` 호출
2. 캐시 미존재 시 기본값 반환 + 경고 로그
3. 응답에 `is_approximate: true` 추가

```python
async def get_distribution(self, force_recalculate: bool = False) -> Dict[str, Any]:
    # ... 기존 캐시 로직 ...

    if needs_recalc:
        logger.info("Recalculating distribution from sketch (approximate)...")
        result = await self.supabase.calculate_distribution_from_sketch()

        if not result.get("success"):
            # 캐시 미존재 시 기본값 반환
            logger.warning("Distribution calculation failed, using defaults")
            return self._get_default_distribution()

    db_cache = await self.supabase.get_similarity_distribution_cache()
    db_cache["is_approximate"] = True  # 명시적 표시
    return db_cache
```

### 3.3 supabase_service.py 수정

**추가 메서드**:
- `build_distribution_sketch()`
- `calculate_distribution_from_sketch()`
- `mine_candidate_pairs()`

**제거 메서드**:
- `get_candidates_from_distance_table()` (Distance Table 의존)
- `calculate_distribution_from_distance_table()` (deprecated)

---

## Phase 4: 엔드포인트 변경

### 4.1 새 엔드포인트

| 엔드포인트 | 메서드 | 설명 |
|-----------|--------|------|
| `/mine-candidates/batch` | POST | 단일 배치 마이닝 |
| `/mine-candidates/full` | POST | 전체 마이닝 (백그라운드) |
| `/mine-candidates/progress` | GET | 진행 상태 조회 |
| `/distribution/sketch/build` | POST | 분포 샘플 수집 |
| `/distribution/sketch/calculate` | POST | 분포 계산 |

### 4.2 수정 엔드포인트

| 엔드포인트 | 변경 내용 |
|-----------|----------|
| `/distribution` | `is_approximate: true` 추가, "approx global distribution" 표시 |
| `/collect-candidates` | `use_distance_table` 파라미터 제거, 마이닝 기반으로 전환 |

### 4.3 삭제 엔드포인트

| 엔드포인트 |
|-----------|
| `/distance-table/build` |
| `/distance-table/status` |
| `/distance-table/update` |

---

## Phase 5: 정리 작업

### 5.1 삭제할 파일

| 파일 |
|------|
| `backend/services/distance_table_service.py` |
| `backend/docs/supabase_migrations/010_create_distance_table.sql` |
| `backend/docs/supabase_migrations/011_build_distance_table_rpc.sql` |
| `backend/docs/supabase_migrations/011_v2_build_distance_table_rpc.sql` |
| `backend/docs/supabase_migrations/012_incremental_update_rpc.sql` |
| `backend/docs/supabase_migrations/013_calculate_distribution_from_distance_table.sql` |
| `backend/docs/supabase_migrations/014_fill_missing_pairs.sql` |

### 5.2 Supabase 정리 SQL

```sql
-- Distance Table 관련 모두 삭제
DROP TABLE IF EXISTS thought_pair_distances CASCADE;
DROP FUNCTION IF EXISTS build_distance_table_batch CASCADE;
DROP FUNCTION IF EXISTS update_distance_table_incremental CASCADE;
DROP FUNCTION IF EXISTS fill_missing_pairs_for_range CASCADE;
DROP FUNCTION IF EXISTS calculate_distribution_from_distance_table CASCADE;

-- pair_candidates 초기화
TRUNCATE pair_candidates RESTART IDENTITY CASCADE;

-- 오래된 샘플 정리 (선택)
DELETE FROM similarity_samples WHERE created_at < NOW() - INTERVAL '7 days';
```

---

## Phase 6: 검증 계획

### 6.1 성능 검증 (60초 제한)

```sql
-- 마이닝 RPC 시간 확인
EXPLAIN ANALYZE SELECT mine_candidate_pairs(0, 30, 1200, 15, 0.10, 0.35, 42, 3);
-- 기대: < 10초

-- 분포 스케치 빌드 시간
EXPLAIN ANALYZE SELECT build_distribution_sketch(42, 200, 500, 1, true);
-- 기대: < 5초

-- 분포 계산 시간
EXPLAIN ANALYZE SELECT calculate_distribution_from_sketch();
-- 기대: < 3초
```

### 6.2 후보 품질 검증

```sql
-- 동일 메모 제외 확인
SELECT COUNT(*) FROM pair_candidates pc
JOIN thought_units ta ON pc.thought_a_id = ta.id
JOIN thought_units tb ON pc.thought_b_id = tb.id
WHERE ta.raw_note_id = tb.raw_note_id;
-- 기대: 0

-- src당 후보 수 분포
SELECT thought_a_id, COUNT(*) AS cnt
FROM pair_candidates
GROUP BY thought_a_id
ORDER BY cnt DESC
LIMIT 20;
-- 기대: 대부분 10-20개

-- 유사도 분포 확인
SELECT
    ROUND(similarity::NUMERIC, 2) AS bucket,
    COUNT(*)
FROM pair_candidates
GROUP BY bucket
ORDER BY bucket;
-- 기대: p_lo ~ p_hi 범위에 집중
```

### 6.3 전역 분포 스케치 검증

```sql
-- 샘플 수에 따른 분포 안정성
-- 1000개 vs 10000개 vs 100000개 샘플의 p10, p50, p90 비교
-- 기대: 샘플 증가에 따라 수렴

-- seed 변경에 따른 분포 변동 측정
-- 기대: ±0.01 이내 변동
```

---

## 구현 순서 체크리스트

### Step 1: DDL 배포 (1일차)
- [ ] 015_add_rand_key.sql 배포
- [ ] 016_create_mining_progress.sql 배포
- [ ] 017_create_similarity_samples.sql 배포
- [ ] ANALYZE 실행

### Step 2: RPC 함수 배포 (1일차)
- [ ] 018_mine_candidate_pairs_rpc.sql 배포
- [ ] 019_build_distribution_sketch_rpc.sql 배포
- [ ] 020_calculate_distribution_from_sketch_rpc.sql 배포
- [ ] EXPLAIN ANALYZE로 성능 확인

### Step 3: 초기 분포 스케치 생성 (1일차)
- [ ] build_distribution_sketch() 실행 (10만 샘플 목표)
- [ ] calculate_distribution_from_sketch() 실행
- [ ] /distribution 엔드포인트 정상 작동 확인

### Step 4: Python 서비스 구현 (2일차)
- [ ] candidate_mining_service.py 생성
- [ ] distribution_service.py 수정 (스케치 기반)
- [ ] supabase_service.py 수정 (새 RPC 호출)

### Step 5: 엔드포인트 구현 (2일차)
- [ ] /mine-candidates/* 엔드포인트 추가
- [ ] /distribution/sketch/* 엔드포인트 추가
- [ ] 기존 엔드포인트 수정

### Step 6: 정리 작업 (3일차)
- [ ] distance_table_service.py 삭제
- [ ] Distance Table SQL 파일 삭제
- [ ] Supabase에서 테이블/함수 DROP
- [ ] CLAUDE.md 업데이트

### Step 7: 전체 마이닝 실행 (3일차)
- [ ] /mine-candidates/full 실행
- [ ] 품질 검증
- [ ] 프론트엔드 연동 테스트

---

## 리스크 및 대응

| 리스크 | 대응 |
|--------|------|
| 마이닝 RPC 60초 초과 | src_batch 줄이기 (30→20), dst_sample 줄이기 |
| 스케치 분포가 실제와 차이 | 샘플 수 증가 (10만→20만) |
| 후보 부족 (k 미달) | max_rounds 증가, p_lo/p_hi 범위 확대 |
| rand_key 인덱스 미활용 | EXPLAIN 확인 후 힌트 추가 또는 쿼리 재작성 |

---

## 핵심 파일 목록

### 신규 생성
1. `backend/docs/supabase_migrations/015_add_rand_key.sql`
2. `backend/docs/supabase_migrations/016_create_mining_progress.sql`
3. `backend/docs/supabase_migrations/017_create_similarity_samples.sql`
4. `backend/docs/supabase_migrations/018_mine_candidate_pairs_rpc.sql`
5. `backend/docs/supabase_migrations/019_build_distribution_sketch_rpc.sql`
6. `backend/docs/supabase_migrations/020_calculate_distribution_from_sketch_rpc.sql`
7. `backend/services/candidate_mining_service.py`

### 수정
8. `backend/services/supabase_service.py`
9. `backend/services/distribution_service.py`
10. `backend/routers/pipeline.py`
11. `CLAUDE.md`

### 삭제
12. `backend/services/distance_table_service.py`
13. `backend/docs/supabase_migrations/010_*.sql` ~ `014_*.sql`
