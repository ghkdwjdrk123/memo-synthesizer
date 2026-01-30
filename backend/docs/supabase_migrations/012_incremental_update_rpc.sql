-- Migration: 012_incremental_update_rpc.sql
-- Purpose: Distance Table 증분 갱신 RPC 함수
-- Created: 2026-01-29
-- Performance: 10개 신규 thought → ~2초

-- Distance Table 증분 갱신 함수
CREATE OR REPLACE FUNCTION update_distance_table_incremental(
    new_thought_ids INTEGER[] DEFAULT NULL
)
RETURNS jsonb AS $$
DECLARE
    v_new_thought_count INTEGER;
    v_new_pairs_inserted INTEGER := 0;
    v_new_thought_id INTEGER;
    v_start_time TIMESTAMPTZ;
    v_end_time TIMESTAMPTZ;
    v_duration_ms INTEGER;
    v_new_x_existing_pairs INTEGER := 0;
    v_new_x_new_pairs INTEGER := 0;
BEGIN
    v_start_time := clock_timestamp();

    -- 1. 신규 thought 자동 감지 (파라미터 없을 경우)
    IF new_thought_ids IS NULL THEN
        SELECT ARRAY_AGG(tu.id) INTO new_thought_ids
        FROM thought_units tu
        WHERE tu.embedding IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM thought_pair_distances tpd
              WHERE tpd.thought_a_id = tu.id OR tpd.thought_b_id = tu.id
          );
    END IF;

    v_new_thought_count := COALESCE(array_length(new_thought_ids, 1), 0);

    -- 신규 thought 없으면 즉시 반환
    IF v_new_thought_count = 0 THEN
        RETURN jsonb_build_object(
            'success', true,
            'new_thought_count', 0,
            'new_pairs_inserted', 0,
            'message', 'No new thoughts to process'
        );
    END IF;

    -- 2. 신규 × 기존 페어 생성 (O(신규 × 기존))
    -- 각 신규 thought에 대해 LOOP 처리
    FOREACH v_new_thought_id IN ARRAY new_thought_ids
    LOOP
        WITH inserted_pairs AS (
            INSERT INTO thought_pair_distances (thought_a_id, thought_b_id, similarity)
            SELECT
                LEAST(new_t.id, existing_t.id) AS thought_a_id,
                GREATEST(new_t.id, existing_t.id) AS thought_b_id,
                (1 - (new_t.embedding <=> existing_t.embedding))::FLOAT AS similarity
            FROM (
                SELECT id, embedding, raw_note_id
                FROM thought_units
                WHERE id = v_new_thought_id
                  AND embedding IS NOT NULL
            ) new_t
            CROSS JOIN thought_units existing_t
            WHERE existing_t.id != new_t.id
              AND existing_t.embedding IS NOT NULL
              AND existing_t.raw_note_id != new_t.raw_note_id
            ON CONFLICT (thought_a_id, thought_b_id) DO NOTHING
            RETURNING 1
        )
        SELECT COUNT(*) INTO v_new_x_existing_pairs
        FROM inserted_pairs;

        v_new_pairs_inserted := v_new_pairs_inserted + v_new_x_existing_pairs;
    END LOOP;

    -- 3. 신규 × 신규 페어 생성 (신규가 2개 이상일 때)
    IF v_new_thought_count > 1 THEN
        WITH inserted_new_pairs AS (
            INSERT INTO thought_pair_distances (thought_a_id, thought_b_id, similarity)
            SELECT
                LEAST(a.id, b.id) AS thought_a_id,
                GREATEST(a.id, b.id) AS thought_b_id,
                (1 - (a.embedding <=> b.embedding))::FLOAT AS similarity
            FROM thought_units a
            CROSS JOIN thought_units b
            WHERE a.id = ANY(new_thought_ids)
              AND b.id = ANY(new_thought_ids)
              AND a.id < b.id  -- 중복 방지
              AND a.embedding IS NOT NULL
              AND b.embedding IS NOT NULL
              AND a.raw_note_id != b.raw_note_id
            ON CONFLICT (thought_a_id, thought_b_id) DO NOTHING
            RETURNING 1
        )
        SELECT COUNT(*) INTO v_new_x_new_pairs
        FROM inserted_new_pairs;

        v_new_pairs_inserted := v_new_pairs_inserted + v_new_x_new_pairs;
    END IF;

    -- 4. 통계 갱신 (쿼리 플래너 최적화)
    ANALYZE thought_pair_distances;

    v_end_time := clock_timestamp();
    v_duration_ms := EXTRACT(EPOCH FROM (v_end_time - v_start_time)) * 1000;

    RETURN jsonb_build_object(
        'success', true,
        'new_thought_count', v_new_thought_count,
        'new_pairs_inserted', v_new_pairs_inserted,
        'new_x_existing_pairs', v_new_x_existing_pairs,
        'new_x_new_pairs', v_new_x_new_pairs,
        'duration_ms', v_duration_ms
    );
EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', SQLERRM,
            'new_thought_count', v_new_thought_count
        );
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION update_distance_table_incremental IS
'Distance Table 증분 갱신 (O(신규 × 기존))

성능 특성:
- 10개 신규 thought × 1,921 기존 = ~2초
- 100개 신규 thought × 1,921 기존 = ~20초
- 복잡도: O(N_new × N_existing)

사용법:
-- 자동 감지 (파라미터 없음)
SELECT update_distance_table_incremental();

-- 수동 지정 (특정 thought IDs)
SELECT update_distance_table_incremental(ARRAY[1001, 1002, 1003]);

-- Python 예시:
-- result = await client.rpc("update_distance_table_incremental")
-- print(f"Added {result["new_pairs_inserted"]} pairs for {result["new_thought_count"]} new thoughts")

갱신 로직:
1. 신규 thought 감지
   - 파라미터 제공: 명시된 IDs 사용
   - 파라미터 없음: thought_pair_distances에 없는 thought 자동 감지

2. 신규 × 기존 페어 생성
   - CROSS JOIN: (각 신규 thought) × (모든 기존 thoughts)
   - 조건: thought_a_id < thought_b_id, raw_note_id 다름
   - 중복 방지: ON CONFLICT DO NOTHING

3. 신규 × 신규 페어 생성 (신규 2개 이상)
   - CROSS JOIN: (신규 thoughts) × (신규 thoughts)
   - 조건: a.id < b.id, raw_note_id 다름

4. 통계 갱신
   - ANALYZE thought_pair_distances (쿼리 플래너 최적화)

Returns:
{
  "success": true/false,
  "new_thought_count": 10,
  "new_pairs_inserted": 19210,
  "new_x_existing_pairs": 19110,
  "new_x_new_pairs": 100,
  "duration_ms": 2345
}

자동 호출 시점:
- /pipeline/extract-thoughts 완료 후 (auto_update_distance_table=true)
- 신규 thought 10개 이상일 때 자동 실행
- 수동 트리거: /pipeline/distance-table/update

Trade-offs:
- 신규 10개: 2초 (즉시 실행 가능)
- 신규 100개: 20초 (백그라운드 권장)
- 신규 1000개: 전체 재구축 권장 (build_distance_table_batch)';
