-- ============================================================
-- 019: build_distribution_sketch RPC 함수
-- ============================================================
-- 목적: 전역 분포 근사를 위한 랜덤 샘플 수집
-- 방식: rand_key 기반 결정론적 샘플링으로 유사도 샘플 저장
-- 핵심: 전쌍 계산 없이 분포 스케치 구축
-- ============================================================

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

    -- 전체 thought 수 확인
    SELECT COUNT(*) INTO v_total_thoughts
    FROM thought_units WHERE embedding IS NOT NULL;

    IF v_total_thoughts = 0 THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'No thoughts with embeddings found'
        );
    END IF;

    -- 라운드 반복
    WHILE v_round <= p_rounds LOOP
        -- rand_key 시작점 계산 (src, dst 다른 시드 사용)
        v_src_rand_start := (v_current_seed::BIGINT % 1000000)::FLOAT / 1000000.0;
        v_dst_rand_start := ((v_current_seed + 500000)::BIGINT % 1000000)::FLOAT / 1000000.0;

        WITH src_sample AS (
            -- 랜덤 src 샘플
            SELECT id, embedding, raw_note_id
            FROM thought_units
            WHERE embedding IS NOT NULL
              AND rand_key >= v_src_rand_start
            ORDER BY rand_key
            LIMIT p_src_sample
        ),
        dst_sample AS (
            -- 랜덤 dst 샘플 (다른 seed)
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
        v_current_seed := v_current_seed + 618033;  -- 황금비 기반 시드 증가
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

-- 코멘트
COMMENT ON FUNCTION build_distribution_sketch IS
'전역 분포 스케치용 유사도 샘플 수집

파라미터:
- p_seed: 결정론적 샘플링용 시드 (기본 42)
- p_src_sample: src 샘플 크기 (기본 200)
- p_dst_sample: dst 샘플 크기 (기본 500)
- p_rounds: 샘플링 라운드 수 (기본 1)
- p_exclude_same_memo: 같은 메모 제외 여부 (기본 TRUE)
- p_policy: 샘플링 정책명 (기본 random_pairs)

권장 설정 (10만 샘플):
- 방법 1: src=200, dst=500, rounds=1 → 100,000 샘플
- 방법 2: src=100, dst=500, rounds=2 → 100,000 샘플

반환값:
- run_id: 이번 실행 식별자
- inserted_samples: 삽입된 샘플 수
- coverage_estimate: 전체 페어 대비 커버리지 (%)
- duration_ms: 실행 시간(ms)

사용 예시:
SELECT build_distribution_sketch(42, 200, 500, 1, true, ''random_pairs'');';
