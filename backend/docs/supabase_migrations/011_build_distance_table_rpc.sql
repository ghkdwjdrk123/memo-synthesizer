-- Migration: 011_build_distance_table_rpc.sql
-- Purpose: Distance Table 초기 구축 - 단일 배치 처리 RPC 함수
-- Created: 2026-01-29
-- Performance: 각 배치 ~10초 (batch_size=50), Python에서 순차 호출
-- Total Time: 1,921개 기준 39회 호출 → ~7분

-- 단일 배치 처리 함수 (타임아웃 회피용)
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

    -- 단일 배치만 INSERT (~10초, 60초 미만 보장)
    -- CROSS JOIN으로 (batch 내 thought) × (모든 thought) 페어 생성
    INSERT INTO thought_pair_distances (thought_a_id, thought_b_id, similarity)
    SELECT
        a.id AS thought_a_id,
        b.id AS thought_b_id,
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
    WHERE b.id > a.id  -- 중복 방지: (a, b) 페어만 생성, (b, a) 제외
      AND b.embedding IS NOT NULL
      AND b.raw_note_id != a.raw_note_id  -- 같은 메모 내 페어 제외
    ON CONFLICT (thought_a_id, thought_b_id) DO NOTHING;  -- 중복 방지

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
'Distance Table 단일 배치 처리 (Python 순차 호출용)

성능 특성:
- 각 배치 실행 시간: ~10초 (batch_size=50)
- 타임아웃 안전: 각 배치 60초 미만 보장
- Python에서 순차적으로 여러 번 호출 (동기 처리)

사용법:
-- 1,921개 thoughts 기준 (batch_size=50)
-- 필요한 호출 횟수: CEIL(1921 / 50) = 39회
-- 예상 총 시간: 39 × 10초 = ~7분

-- Python 예시:
-- for offset in range(0, 1921, 50):
--     await client.rpc("build_distance_table_batch", {"batch_offset": offset, "batch_size": 50})

배치 처리 로직:
1. OFFSET/LIMIT으로 배치 내 thoughts 선택
2. CROSS JOIN으로 (배치 thoughts) × (전체 thoughts) 페어 생성
3. 조건: thought_a_id < thought_b_id, raw_note_id 다름
4. 유사도 계산: 1 - cosine_distance (코사인 유사도)

Parameters:
- batch_offset: 시작 위치 (0, 50, 100, ...)
- batch_size: 배치 크기 (권장: 50, 범위: 25-100)

Returns:
{
  "success": true/false,
  "pairs_inserted": 12345,
  "batch_offset": 0,
  "batch_size": 50,
  "duration_ms": 10234
}

Trade-offs:
- 순차 처리 (동기): 안정성 우선, DB 부하 안정, 연결 제한 회피
- 비동기 처리: 2-3분 가능하지만 Free tier 연결 제한으로 위험';
