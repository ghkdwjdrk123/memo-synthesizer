-- Migration: 011_build_distance_table_rpc_v2.sql
-- Purpose: Distance Table 초기 구축 - 배치 처리 로직 수정 (중복 제거 방식 변경)
-- Created: 2026-01-29
-- Fixed: WHERE b.id > a.id 조건이 배치 처리에서 페어 누락 발생 → 전체 CROSS JOIN + 중복 제거

-- 단일 배치 처리 함수 (수정 버전)
CREATE OR REPLACE FUNCTION build_distance_table_batch(
    batch_offset INTEGER,
    batch_size INTEGER
)
RETURNS jsonb AS $$
DECLARE
    v_pairs_inserted INTEGER := 0;
    v_start_time TIMESTAMPTZ;
    v_end_time TIMESTAMPTZ;
    v_duration_ms INTEGER;
BEGIN
    v_start_time := clock_timestamp();

    -- 배치 내 모든 thoughts에 대해 전체 thoughts와 CROSS JOIN
    -- 중복 제거: LEAST/GREATEST로 정렬 보장 + UNIQUE 제약조건
    INSERT INTO thought_pair_distances (thought_a_id, thought_b_id, similarity)
    SELECT
        LEAST(a.id, b.id) AS thought_a_id,      -- 항상 작은 ID가 thought_a
        GREATEST(a.id, b.id) AS thought_b_id,   -- 항상 큰 ID가 thought_b
        (1 - (a.embedding <=> b.embedding))::FLOAT AS similarity
    FROM (
        -- 배치: OFFSET/LIMIT으로 일부만 선택
        SELECT id, embedding, raw_note_id
        FROM thought_units
        WHERE embedding IS NOT NULL
        ORDER BY id
        LIMIT batch_size OFFSET batch_offset
    ) a
    CROSS JOIN thought_units b
    WHERE a.id != b.id                          -- 자기 자신 제외
      AND b.embedding IS NOT NULL
      AND a.raw_note_id != b.raw_note_id        -- 같은 메모 내 페어 제외
    ON CONFLICT (thought_a_id, thought_b_id) DO NOTHING;  -- 중복 방지 (UNIQUE 제약)

    GET DIAGNOSTICS v_pairs_inserted = ROW_COUNT;

    v_end_time := clock_timestamp();
    v_duration_ms := EXTRACT(EPOCH FROM (v_end_time - v_start_time)) * 1000;

    RETURN jsonb_build_object(
        'success', true,
        'pairs_inserted', v_pairs_inserted,
        'batch_offset', batch_offset,
        'batch_size', batch_size,
        'duration_ms', v_duration_ms
    );
EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', SQLERRM,
            'batch_offset', batch_offset,
            'batch_size', batch_size
        );
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION build_distance_table_batch IS
'Distance Table 단일 배치 처리 (v2: 중복 제거 수정)

주요 변경사항:
- 이전: WHERE b.id > a.id (배치 처리 시 페어 누락 발생)
- 현재: WHERE a.id != b.id + LEAST/GREATEST + UNIQUE 제약 (모든 페어 생성)

성능 특성:
- 각 배치 실행 시간: ~10-15초 (batch_size=50)
- 타임아웃 안전: 각 배치 60초 미만 보장
- Python에서 순차적으로 여러 번 호출 (동기 처리)

작동 방식:
1. 배치 내 thoughts 선택 (OFFSET/LIMIT)
2. 전체 thoughts와 CROSS JOIN
3. LEAST/GREATEST로 정렬 보장 (thought_a_id < thought_b_id)
4. UNIQUE 제약조건으로 중복 자동 제거
5. 같은 메모 내 페어는 raw_note_id로 필터링

예상 결과 (1,909 thoughts):
- 총 페어 수: 1,909 × 1,908 / 2 = 1,821,186개
- 배치 크기 50 기준: 39회 호출
- 예상 총 시간: 39 × 12초 = ~8분

이전 버전 문제:
- WHERE b.id > a.id 조건으로 인해 75% 페어 누락
- 실제 455,124개만 생성 (예상 1,821,186개)
- thought_a_id 범위: 251-1908 (1-250 완전 누락)';
