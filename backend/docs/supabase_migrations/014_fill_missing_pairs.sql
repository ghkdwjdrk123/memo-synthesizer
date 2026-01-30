-- ============================================================
-- 014: 누락된 페어 찾아서 채우기
-- ============================================================
--
-- 문제: 배치 처리 중 타임아웃으로 일부 페어 누락
-- 해결: 누락된 페어를 직접 계산하여 INSERT
--
-- 예상 누락: ~48,000쌍 (전체 1,819,386 중 2.6%)
-- ============================================================

-- 1. 누락된 페어 개수 확인 (실행 전 검증용)
-- 주의: 이 쿼리는 매우 느릴 수 있음 (CROSS JOIN)
/*
SELECT
    (SELECT COUNT(*)
     FROM thought_units a
     CROSS JOIN thought_units b
     WHERE a.id < b.id
       AND a.embedding IS NOT NULL
       AND b.embedding IS NOT NULL
       AND a.raw_note_id != b.raw_note_id
    ) - (SELECT COUNT(*) FROM thought_pair_distances) AS missing_pairs;
*/

-- 2. 누락된 페어 채우기 (배치 처리)
-- 전략: 특정 thought_a_id 범위별로 누락된 페어만 INSERT
-- 타임아웃 방지를 위해 작은 범위로 분할 실행

-- 함수: 특정 thought_a_id 범위의 누락된 페어 채우기
CREATE OR REPLACE FUNCTION fill_missing_pairs_for_range(
    start_id INTEGER,
    end_id INTEGER
)
RETURNS jsonb AS $$
DECLARE
    v_pairs_inserted INTEGER := 0;
    v_start_time TIMESTAMPTZ;
    v_end_time TIMESTAMPTZ;
    v_duration_ms INTEGER;
BEGIN
    v_start_time := clock_timestamp();

    -- 누락된 페어만 INSERT
    -- NOT EXISTS로 이미 존재하는 페어 제외
    INSERT INTO thought_pair_distances (thought_a_id, thought_b_id, similarity)
    SELECT
        a.id AS thought_a_id,
        b.id AS thought_b_id,
        (1 - (a.embedding <=> b.embedding))::FLOAT AS similarity
    FROM thought_units a
    CROSS JOIN thought_units b
    WHERE a.id >= start_id
      AND a.id <= end_id
      AND a.id < b.id                              -- 정렬 보장 (a < b)
      AND a.embedding IS NOT NULL
      AND b.embedding IS NOT NULL
      AND a.raw_note_id != b.raw_note_id           -- 같은 메모 제외
      AND NOT EXISTS (
          SELECT 1 FROM thought_pair_distances tpd
          WHERE tpd.thought_a_id = a.id AND tpd.thought_b_id = b.id
      );

    GET DIAGNOSTICS v_pairs_inserted = ROW_COUNT;

    v_end_time := clock_timestamp();
    v_duration_ms := EXTRACT(EPOCH FROM (v_end_time - v_start_time)) * 1000;

    RETURN jsonb_build_object(
        'success', true,
        'pairs_inserted', v_pairs_inserted,
        'start_id', start_id,
        'end_id', end_id,
        'duration_ms', v_duration_ms
    );
EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', SQLERRM,
            'start_id', start_id,
            'end_id', end_id
        );
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION fill_missing_pairs_for_range IS
'특정 thought_a_id 범위의 누락된 페어 채우기

사용법:
  SELECT fill_missing_pairs_for_range(1, 100);
  SELECT fill_missing_pairs_for_range(101, 200);
  ...

특징:
- NOT EXISTS로 누락된 페어만 찾아서 INSERT
- 기존 페어는 건너뜀 (중복 방지)
- 작은 범위로 분할하여 타임아웃 방지

권장 범위:
- 100개씩 분할 (예: 1-100, 101-200, ...)
- 각 호출 예상 시간: 10-30초';
