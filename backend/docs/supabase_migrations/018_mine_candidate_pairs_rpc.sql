-- ============================================================
-- 018: mine_candidate_pairs RPC 함수
-- ============================================================
-- 목적: src당 10-20개 후보 페어 생성 (샘플링 기반)
-- 핵심 제약:
--   - 60초 타임아웃 준수
--   - OFFSET 금지 → 키셋 페이징 (id > last_src_id)
--   - ORDER BY random() 금지 → rand_key 기반 샘플링
--   - 같은 memo_id 페어 제외
-- ============================================================

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
            -- seed 기반 결정론적 샘플링
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
        -- 밴드 값 저장 (RETURNING에서 사용)
        band_values AS (
            SELECT band_lo, band_hi FROM band_calc
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

        -- 밴드 값 저장 (마지막 라운드 기록용)
        SELECT band_lo, band_hi INTO v_band_lo, v_band_hi
        FROM (
            SELECT
                PERCENTILE_CONT(p_lo) WITHIN GROUP (ORDER BY similarity) AS band_lo,
                PERCENTILE_CONT(p_hi) WITHIN GROUP (ORDER BY similarity) AS band_hi
            FROM (
                SELECT (1 - (src.embedding <=> dst.embedding))::FLOAT AS similarity
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

        -- 50% 이상 생성되면 충분
        IF v_total_inserted >= (v_src_count * p_k * 0.5) THEN
            EXIT;
        END IF;

        -- seed 변경 (황금비 기반)
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

-- 코멘트
COMMENT ON FUNCTION mine_candidate_pairs IS
'샘플링 기반 후보 페어 마이닝

파라미터:
- p_last_src_id: 마지막 처리한 src ID (키셋 페이징)
- p_src_batch: 배치당 src 수 (기본 30)
- p_dst_sample: dst 샘플 크기 (기본 1200)
- p_k: src당 후보 수 (기본 15)
- p_lo/p_hi: 분위수 범위 (기본 0.10~0.35)
- p_seed: 결정론적 샘플링용 시드 (기본 42)
- p_max_rounds: 최대 재시도 횟수 (기본 3)

반환값:
- success: 성공 여부
- new_last_src_id: 다음 배치 시작점
- inserted_count: 삽입된 후보 수
- src_processed_count: 처리된 src 수
- rounds_used: 사용된 라운드 수
- band_lo/band_hi: 사용된 유사도 밴드
- avg_candidates_per_src: src당 평균 후보 수
- duration_ms: 실행 시간(ms)

사용 예시:
SELECT mine_candidate_pairs(0, 30, 1200, 15, 0.10, 0.35, 42, 3);
-- 다음 배치: new_last_src_id를 p_last_src_id로 전달';
