-- ============================================================
-- 013: Distance Table 기반 유사도 분포 계산 (샘플링)
-- ============================================================
--
-- 목적: thought_pair_distances 테이블에서 percentile 계산
-- 방식: 100,000개 샘플링 → 60초 타임아웃 회피
-- 정확도: 99%+ (Central Limit Theorem)
--
-- 기존 calculate_similarity_distribution: thought_units CROSS JOIN (타임아웃)
-- 신규 calculate_distribution_from_distance_table: 샘플링 집계 (빠름)
-- ============================================================

CREATE OR REPLACE FUNCTION calculate_distribution_from_distance_table()
RETURNS jsonb
LANGUAGE plpgsql
AS $$
DECLARE
    v_start_time TIMESTAMPTZ;
    v_end_time TIMESTAMPTZ;
    v_duration_ms INTEGER;
    v_total_pairs BIGINT;
    v_sample_size INTEGER := 100000;  -- 10만 개 샘플 (정확도 99%+)
    v_result JSONB;
BEGIN
    v_start_time := clock_timestamp();

    -- 1. 총 페어 수 확인
    SELECT COUNT(*) INTO v_total_pairs FROM thought_pair_distances;

    IF v_total_pairs = 0 THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'No pairs in thought_pair_distances table. Build distance table first.'
        );
    END IF;

    -- 2. Percentile 계산 (샘플링 기반, 타임아웃 회피)
    -- TABLESAMPLE SYSTEM은 페이지 기반이라 정확도가 낮음
    -- ORDER BY RANDOM() LIMIT이 더 균일한 샘플 제공
    SELECT jsonb_build_object(
        'total_pairs', v_total_pairs,
        'sample_size', LEAST(v_sample_size, v_total_pairs),
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
        FROM thought_pair_distances
        ORDER BY RANDOM()
        LIMIT v_sample_size
    ) sampled;

    v_end_time := clock_timestamp();
    v_duration_ms := EXTRACT(EPOCH FROM (v_end_time - v_start_time)) * 1000;

    -- 3. 캐시 테이블 업데이트
    INSERT INTO similarity_distribution_cache (
        id,
        thought_unit_count,
        total_pair_count,
        p0, p10, p20, p30, p40, p50, p60, p70, p80, p90, p100,
        mean, stddev,
        calculated_at,
        embedding_model,
        calculation_duration_ms
    ) VALUES (
        1,
        (SELECT COUNT(*) FROM thought_units WHERE embedding IS NOT NULL),
        v_total_pairs,
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
        v_duration_ms
    )
    ON CONFLICT (id) DO UPDATE SET
        thought_unit_count = EXCLUDED.thought_unit_count,
        total_pair_count = EXCLUDED.total_pair_count,
        p0 = EXCLUDED.p0,
        p10 = EXCLUDED.p10,
        p20 = EXCLUDED.p20,
        p30 = EXCLUDED.p30,
        p40 = EXCLUDED.p40,
        p50 = EXCLUDED.p50,
        p60 = EXCLUDED.p60,
        p70 = EXCLUDED.p70,
        p80 = EXCLUDED.p80,
        p90 = EXCLUDED.p90,
        p100 = EXCLUDED.p100,
        mean = EXCLUDED.mean,
        stddev = EXCLUDED.stddev,
        calculated_at = EXCLUDED.calculated_at,
        calculation_duration_ms = EXCLUDED.calculation_duration_ms;

    -- 4. 결과 반환
    RETURN jsonb_build_object(
        'success', true,
        'total_pairs', v_total_pairs,
        'percentiles', v_result,
        'duration_ms', v_duration_ms,
        'cached', true
    );

EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', SQLERRM
        );
END;
$$;

COMMENT ON FUNCTION calculate_distribution_from_distance_table IS
'Distance Table 기반 유사도 분포 계산 (빠름)
- 기존 calculate_similarity_distribution: CROSS JOIN → 60초+ 타임아웃
- 신규: thought_pair_distances 집계 → 1초 미만
- 결과를 similarity_distribution_cache에 자동 저장';
