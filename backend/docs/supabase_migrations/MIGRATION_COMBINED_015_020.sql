-- ============================================================
-- 통합 마이그레이션: 샘플링 기반 후보 마이닝 + 전역 분포 스케치
-- ============================================================
-- 실행 순서: 015 → 016 → 017 → 018 → 019 → 020
-- 예상 실행 시간: ~30초
--
-- 주의사항:
-- 1. Supabase SQL Editor에서 실행
-- 2. 기존 Distance Table 코드와 독립적으로 실행 가능
-- 3. 실행 후 ANALYZE 권장
-- ============================================================


-- ============================================================
-- 015: thought_units에 rand_key 컬럼 추가
-- ============================================================
-- 목적: 빠른 결정론적 샘플링 지원
-- ORDER BY random() 대신 rand_key 범위 조회로 인덱스 활용

-- 1. rand_key 컬럼 추가
ALTER TABLE thought_units
ADD COLUMN IF NOT EXISTS rand_key DOUBLE PRECISION DEFAULT random();

-- 2. 기존 레코드에 rand_key 값 채우기
UPDATE thought_units
SET rand_key = random()
WHERE rand_key IS NULL;

-- 3. NOT NULL 제약 추가
ALTER TABLE thought_units
ALTER COLUMN rand_key SET NOT NULL;

-- 4. 빠른 샘플링용 인덱스
CREATE INDEX IF NOT EXISTS idx_thought_units_rand_key
ON thought_units (rand_key);

-- 5. 코멘트
COMMENT ON COLUMN thought_units.rand_key IS
'결정론적 샘플링용 랜덤 키. seed 기반 범위 조회로 ORDER BY random() 대체.
사용법: WHERE rand_key >= (seed % 1000000) / 1000000.0 ORDER BY rand_key LIMIT n';


-- ============================================================
-- 016: pair_mining_progress 테이블 생성
-- ============================================================
-- 목적: 마이닝 진행 상태 추적 및 재개 지원

CREATE TABLE IF NOT EXISTS pair_mining_progress (
    id SERIAL PRIMARY KEY,
    run_id UUID DEFAULT gen_random_uuid(),

    -- 진행 상태
    last_src_id INTEGER NOT NULL DEFAULT 0,
    total_src_processed INTEGER NOT NULL DEFAULT 0,
    total_pairs_inserted BIGINT NOT NULL DEFAULT 0,
    avg_candidates_per_src FLOAT,

    -- 파라미터 스냅샷 (재개 시 일관성 보장)
    src_batch INTEGER NOT NULL DEFAULT 30,
    dst_sample INTEGER NOT NULL DEFAULT 1200,
    k_per_src INTEGER NOT NULL DEFAULT 15,
    p_lo FLOAT NOT NULL DEFAULT 0.10,
    p_hi FLOAT NOT NULL DEFAULT 0.35,
    max_rounds INTEGER NOT NULL DEFAULT 3,
    seed INTEGER NOT NULL DEFAULT 42,

    -- 상태
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'in_progress', 'completed', 'paused', 'failed')),
    started_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    error_message TEXT
);

-- 싱글톤 보장: 하나의 활성 작업만 허용
CREATE UNIQUE INDEX IF NOT EXISTS idx_pmp_active
ON pair_mining_progress ((1))
WHERE status = 'in_progress';

-- 최신 진행 상태 조회용
CREATE INDEX IF NOT EXISTS idx_pmp_updated
ON pair_mining_progress (updated_at DESC);

-- 코멘트
COMMENT ON TABLE pair_mining_progress IS
'마이닝 진행 상태 추적. 키셋 페이징(last_src_id)으로 재개 가능.';

COMMENT ON COLUMN pair_mining_progress.last_src_id IS
'마지막 처리한 src thought ID. 다음 배치는 id > last_src_id로 시작.';

COMMENT ON COLUMN pair_mining_progress.avg_candidates_per_src IS
'src당 평균 후보 수. 품질 모니터링용.';


-- ============================================================
-- 017: similarity_samples 테이블 생성 (전역 분포 스케치용)
-- ============================================================
-- 목적: 전쌍 계산 없이 전역 분포를 근사하기 위한 샘플 저장
-- 방식: Raw sample 저장 → PERCENTILE_CONT로 p0-p100 계산

CREATE TABLE IF NOT EXISTS similarity_samples (
    id BIGSERIAL PRIMARY KEY,

    -- 실행 식별자
    run_id UUID NOT NULL,

    -- 샘플 데이터
    similarity FLOAT NOT NULL CHECK (similarity >= 0 AND similarity <= 1),

    -- 디버깅/분석용 (선택적)
    src_id INTEGER,
    dst_id INTEGER,

    -- 샘플링 메타데이터
    seed INTEGER,
    policy TEXT DEFAULT 'random_pairs',

    -- 타임스탬프
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 최신 run의 샘플 조회용
CREATE INDEX IF NOT EXISTS idx_ss_run_id
ON similarity_samples (run_id);

-- 시간순 정렬용
CREATE INDEX IF NOT EXISTS idx_ss_created_at
ON similarity_samples (created_at DESC);

-- 오래된 샘플 정리용 인덱스 (제거됨)
-- 참고: NOW()는 IMMUTABLE이 아니라 partial index에 사용 불가
-- 정리는 스케줄 작업으로 처리: DELETE FROM similarity_samples WHERE created_at < NOW() - INTERVAL '7 days';

-- 코멘트
COMMENT ON TABLE similarity_samples IS
'전역 분포 스케치용 유사도 샘플.
- build_distribution_sketch()로 샘플 수집
- calculate_distribution_from_sketch()로 p0-p100 계산
- 10만개 샘플 권장 (정확도 99%+)
- 7일 이상 오래된 샘플은 정리 권장';

COMMENT ON COLUMN similarity_samples.run_id IS
'샘플링 실행 ID. 같은 run의 샘플끼리 그룹화.';

COMMENT ON COLUMN similarity_samples.policy IS
'샘플링 정책. random_pairs(기본), stratified, etc.';


-- ============================================================
-- 018: mine_candidate_pairs RPC 함수
-- ============================================================
-- 목적: src당 10-20개 후보 페어 생성 (샘플링 기반)

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
    v_total_inserted BIGINT := 0;
    v_round INTEGER := 1;
    v_current_seed INTEGER;
    v_start_time TIMESTAMPTZ;
    v_band_lo FLOAT;
    v_band_hi FLOAT;
    v_rand_start FLOAT;
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
        -- rand_key 시작점 계산 (seed 기반 결정론적)
        v_rand_start := (v_current_seed::BIGINT % 1000000)::FLOAT / 1000000.0;

        -- 2.1 rand_key 기반 dst 샘플링 + 유사도 계산 + 분위수 계산 + 후보 선택
        WITH dst_sample AS (
            SELECT id, embedding, raw_note_id
            FROM thought_units
            WHERE embedding IS NOT NULL
              AND rand_key >= v_rand_start
            ORDER BY rand_key
            LIMIT p_dst_sample
        ),
        similarity_calc AS (
            SELECT
                src.id AS src_id,
                dst.id AS dst_id,
                src.raw_note_id AS src_memo,
                dst.raw_note_id AS dst_memo,
                -- 유사도를 0~1 범위로 클램핑 (부동소수점 오차 방지)
                GREATEST(0, LEAST(1, 1 - (src.embedding <=> dst.embedding)))::FLOAT AS similarity
            FROM thought_units src
            CROSS JOIN dst_sample dst
            WHERE src.id = ANY(v_src_ids)
              AND src.id != dst.id
              AND src.raw_note_id != dst.raw_note_id
        ),
        band_calc AS (
            SELECT
                PERCENTILE_CONT(p_lo) WITHIN GROUP (ORDER BY similarity) AS band_lo,
                PERCENTILE_CONT(p_hi) WITHIN GROUP (ORDER BY similarity) AS band_hi
            FROM similarity_calc
        ),
        band_values AS (
            SELECT band_lo, band_hi FROM band_calc
        ),
        ranked_candidates AS (
            SELECT
                LEAST(sc.src_id, sc.dst_id) AS thought_a_id,
                GREATEST(sc.src_id, sc.dst_id) AS thought_b_id,
                sc.similarity,
                sc.src_memo AS raw_note_id_a,
                sc.dst_memo AS raw_note_id_b,
                ROW_NUMBER() OVER (PARTITION BY sc.src_id ORDER BY sc.similarity) AS rn
            FROM similarity_calc sc, band_values bv
            WHERE sc.similarity BETWEEN bv.band_lo AND bv.band_hi
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
        SELECT COUNT(*) INTO v_inserted_count FROM inserted;

        -- 밴드 값 저장
        SELECT band_lo, band_hi INTO v_band_lo, v_band_hi
        FROM (
            SELECT
                PERCENTILE_CONT(p_lo) WITHIN GROUP (ORDER BY similarity) AS band_lo,
                PERCENTILE_CONT(p_hi) WITHIN GROUP (ORDER BY similarity) AS band_hi
            FROM (
                SELECT GREATEST(0, LEAST(1, 1 - (src.embedding <=> dst.embedding)))::FLOAT AS similarity
                FROM thought_units src
                CROSS JOIN (
                    SELECT id, embedding, raw_note_id
                    FROM thought_units
                    WHERE embedding IS NOT NULL
                      AND rand_key >= v_rand_start
                    ORDER BY rand_key
                    LIMIT p_dst_sample
                ) dst
                WHERE src.id = ANY(v_src_ids)
                  AND src.id != dst.id
                  AND src.raw_note_id != dst.raw_note_id
            ) sim
        ) calc;

        v_total_inserted := v_total_inserted + COALESCE(v_inserted_count, 0);

        IF v_total_inserted >= (v_src_count * p_k * 0.5) THEN
            EXIT;
        END IF;

        v_current_seed := v_current_seed + 618033;
        v_round := v_round + 1;
    END LOOP;

    RETURN jsonb_build_object(
        'success', true,
        'new_last_src_id', v_new_last_src_id,
        'inserted_count', v_total_inserted,
        'src_processed_count', v_src_count,
        'rounds_used', LEAST(v_round, p_max_rounds),
        'band_lo', v_band_lo,
        'band_hi', v_band_hi,
        'avg_candidates_per_src', CASE WHEN v_src_count > 0
            THEN ROUND((v_total_inserted::FLOAT / v_src_count)::NUMERIC, 2)
            ELSE 0 END,
        'duration_ms', ROUND(EXTRACT(EPOCH FROM (clock_timestamp() - v_start_time)) * 1000)
    );

EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', SQLERRM,
            'error_detail', SQLSTATE,
            'last_src_id', p_last_src_id
        );
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION mine_candidate_pairs IS
'샘플링 기반 후보 페어 마이닝. src당 k개 후보 생성.
파라미터: p_last_src_id(키셋), p_src_batch(30), p_dst_sample(1200), p_k(15), p_lo(0.10), p_hi(0.35), p_seed(42), p_max_rounds(3)';


-- ============================================================
-- 019: build_distribution_sketch RPC 함수
-- ============================================================
-- 목적: 전역 분포 근사를 위한 랜덤 샘플 수집

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
    v_round_inserted BIGINT;
    v_round INTEGER := 1;
    v_current_seed INTEGER;
    v_start_time TIMESTAMPTZ;
    v_total_thoughts INTEGER;
    v_src_rand_start FLOAT;
    v_dst_rand_start FLOAT;
BEGIN
    v_start_time := clock_timestamp();
    v_run_id := gen_random_uuid();
    v_current_seed := p_seed;

    SELECT COUNT(*) INTO v_total_thoughts
    FROM thought_units WHERE embedding IS NOT NULL;

    IF v_total_thoughts = 0 THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'No thoughts with embeddings found'
        );
    END IF;

    WHILE v_round <= p_rounds LOOP
        v_src_rand_start := (v_current_seed::BIGINT % 1000000)::FLOAT / 1000000.0;
        v_dst_rand_start := ((v_current_seed + 500000)::BIGINT % 1000000)::FLOAT / 1000000.0;

        WITH src_sample AS (
            SELECT id, embedding, raw_note_id
            FROM thought_units
            WHERE embedding IS NOT NULL
              AND rand_key >= v_src_rand_start
            ORDER BY rand_key
            LIMIT p_src_sample
        ),
        dst_sample AS (
            SELECT id, embedding, raw_note_id
            FROM thought_units
            WHERE embedding IS NOT NULL
              AND rand_key >= v_dst_rand_start
            ORDER BY rand_key
            LIMIT p_dst_sample
        ),
        similarity_calc AS (
            SELECT
                src.id AS src_id,
                dst.id AS dst_id,
                -- 유사도를 0~1 범위로 클램핑 (부동소수점 오차 방지)
                GREATEST(0, LEAST(1, 1 - (src.embedding <=> dst.embedding)))::FLOAT AS similarity
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
        SELECT COUNT(*) INTO v_round_inserted FROM inserted;

        v_inserted_count := v_inserted_count + COALESCE(v_round_inserted, 0);
        v_current_seed := v_current_seed + 618033;
        v_round := v_round + 1;
    END LOOP;

    RETURN jsonb_build_object(
        'success', true,
        'run_id', v_run_id,
        'inserted_samples', v_inserted_count,
        'total_thoughts', v_total_thoughts,
        'coverage_estimate', ROUND(
            (p_src_sample::FLOAT * p_dst_sample * p_rounds) /
            (v_total_thoughts::FLOAT * v_total_thoughts) * 100, 4
        ),
        'params', jsonb_build_object(
            'seed', p_seed,
            'src_sample', p_src_sample,
            'dst_sample', p_dst_sample,
            'rounds', p_rounds,
            'exclude_same_memo', p_exclude_same_memo,
            'policy', p_policy
        ),
        'duration_ms', ROUND(EXTRACT(EPOCH FROM (clock_timestamp() - v_start_time)) * 1000)
    );

EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', SQLERRM,
            'error_detail', SQLSTATE
        );
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION build_distribution_sketch IS
'전역 분포 스케치용 유사도 샘플 수집.
권장: src=200, dst=500, rounds=1 → 10만 샘플';


-- ============================================================
-- 020: calculate_distribution_from_sketch RPC 함수
-- ============================================================
-- 목적: 샘플에서 전역 분포 계산 (p0-p100)

CREATE OR REPLACE FUNCTION calculate_distribution_from_sketch(
    p_run_id UUID DEFAULT NULL,
    p_sample_limit INTEGER DEFAULT 100000
)
RETURNS jsonb AS $$
DECLARE
    v_run_id UUID;
    v_sample_count BIGINT;
    v_thought_count INTEGER;
    v_result JSONB;
    v_start_time TIMESTAMPTZ;
BEGIN
    v_start_time := clock_timestamp();

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

    SELECT COUNT(*) INTO v_sample_count
    FROM similarity_samples
    WHERE run_id = v_run_id;

    IF v_sample_count = 0 THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'No samples found for run_id: ' || v_run_id::TEXT
        );
    END IF;

    SELECT COUNT(*) INTO v_thought_count
    FROM thought_units
    WHERE embedding IS NOT NULL;

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
        LIMIT p_sample_limit
    ) sampled;

    INSERT INTO similarity_distribution_cache (
        id, thought_unit_count, total_pair_count,
        p0, p10, p20, p30, p40, p50, p60, p70, p80, p90, p100,
        mean, stddev, calculated_at, embedding_model, calculation_duration_ms
    ) VALUES (
        1,
        v_thought_count,
        v_sample_count,
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
        COALESCE((v_result->>'stddev')::FLOAT, 0),
        NOW(),
        'text-embedding-3-small',
        ROUND(EXTRACT(EPOCH FROM (clock_timestamp() - v_start_time)) * 1000)
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
        'is_approximate', true,
        'thought_count', v_thought_count,
        'sample_count', v_sample_count,
        'run_id', v_run_id,
        'duration_ms', ROUND(EXTRACT(EPOCH FROM (clock_timestamp() - v_start_time)) * 1000)
    );

EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', SQLERRM,
            'error_detail', SQLSTATE
        );
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION calculate_distribution_from_sketch IS
'샘플 기반 전역 분포 계산. similarity_distribution_cache 갱신.';


-- ============================================================
-- 마이그레이션 완료 후 통계 갱신
-- ============================================================
ANALYZE thought_units;


-- ============================================================
-- 마이그레이션 검증 쿼리 (선택적 실행)
-- ============================================================
-- 아래 쿼리들은 마이그레이션 완료 후 별도로 실행하여 검증

-- 1. rand_key 컬럼 확인
-- SELECT COUNT(*) AS total, COUNT(rand_key) AS with_rand_key FROM thought_units;

-- 2. 새 테이블 확인
-- SELECT COUNT(*) FROM pair_mining_progress;
-- SELECT COUNT(*) FROM similarity_samples;

-- 3. RPC 함수 테스트 (초기 분포 스케치 생성)
-- SELECT build_distribution_sketch(42, 200, 500, 1, true, 'random_pairs');
-- SELECT calculate_distribution_from_sketch();

-- 4. 마이닝 테스트 (첫 배치)
-- SELECT mine_candidate_pairs(0, 10, 500, 5, 0.10, 0.35, 42, 2);
