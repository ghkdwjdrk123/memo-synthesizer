-- 009: 유사도 분포 계산 RPC 함수 생성
-- 목적: Top-K 기반 페어 샘플링 및 백분위수 계산
-- 날짜: 2026-01-26

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

-- 설명
COMMENT ON FUNCTION calculate_similarity_distribution() IS 'Top-K 샘플링 기반 유사도 분포 계산 (P0-P100 백분위수)';
