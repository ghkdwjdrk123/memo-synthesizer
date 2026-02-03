-- ============================================================
-- 020: calculate_distribution_from_sketch RPC 함수
-- ============================================================
-- 목적: 샘플에서 전역 분포 계산 (p0-p100)
-- 방식: PERCENTILE_CONT로 백분위수 계산
-- 결과: similarity_distribution_cache 테이블 갱신
-- ============================================================

CREATE OR REPLACE FUNCTION calculate_distribution_from_sketch(
    p_run_id UUID DEFAULT NULL,  -- NULL이면 최신 사용
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

    IF v_sample_count = 0 THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'No samples found for run_id: ' || v_run_id::TEXT
        );
    END IF;

    -- thought 수 확인
    SELECT COUNT(*) INTO v_thought_count
    FROM thought_units
    WHERE embedding IS NOT NULL;

    -- 백분위수 계산 (샘플 제한 적용)
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

    -- similarity_distribution_cache 갱신 (기존 형식 유지)
    INSERT INTO similarity_distribution_cache (
        id, thought_unit_count, total_pair_count,
        p0, p10, p20, p30, p40, p50, p60, p70, p80, p90, p100,
        mean, stddev, calculated_at, embedding_model, calculation_duration_ms
    ) VALUES (
        1,
        v_thought_count,
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
        'is_approximate', true,  -- 명시적으로 근사값임을 표시
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

-- 코멘트
COMMENT ON FUNCTION calculate_distribution_from_sketch IS
'샘플 기반 전역 분포 계산

파라미터:
- p_run_id: 특정 run의 샘플 사용 (NULL이면 최신)
- p_sample_limit: 최대 샘플 수 (기본 100,000)

반환값:
- distribution: p0, p10, ..., p100, mean, stddev
- is_approximate: true (근사값임을 명시)
- cached: true (similarity_distribution_cache 갱신됨)
- sample_count: 사용된 샘플 수
- duration_ms: 실행 시간(ms)

사용 예시:
-- 최신 스케치로 계산
SELECT calculate_distribution_from_sketch();

-- 특정 run으로 계산
SELECT calculate_distribution_from_sketch(''abc123-uuid'');

주의사항:
- similarity_distribution_cache.total_pair_count는 샘플 수를 저장
- 실제 전쌍 수가 아닌 근사값임을 인지해야 함';
