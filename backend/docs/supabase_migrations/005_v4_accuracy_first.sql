-- Migration: 005_v4_accuracy_first.sql
-- Purpose: 정확도 우선 - 낮은 유사도 페어 수집 (ORDER BY DESC)
-- Created: 2026-01-29
-- Changes: HNSW 인덱스 비활용하지만 정확한 P10-P40 범위 데이터 수집
-- Trade-off: 성능 < 정확도 (weak ties 발견이 핵심 요구사항)

-- Top-K 방식 유사도 검색 함수 (v4: 정확도 우선)
CREATE OR REPLACE FUNCTION find_similar_pairs_topk(
    min_sim FLOAT DEFAULT 0.05,
    max_sim FLOAT DEFAULT 0.35,
    top_k INT DEFAULT 20,
    lim INT DEFAULT 10000
)
RETURNS TABLE (
    thought_a_id INT,
    thought_b_id INT,
    thought_a_claim TEXT,
    thought_b_claim TEXT,
    similarity_score FLOAT,
    raw_note_id_a UUID,
    raw_note_id_b UUID
) AS $$
BEGIN
    -- min_sim, max_sim 파라미터는 하위 호환성을 위해 유지하지만 SQL에서 사용하지 않음
    -- Python 레벨에서 P10-P40 필터링 수행

    RETURN QUERY
    WITH ranked_pairs AS (
        SELECT
            a.id::INT AS thought_a_id,
            b.id::INT AS thought_b_id,
            a.claim AS thought_a_claim,
            b.claim AS thought_b_claim,
            a.raw_note_id AS raw_note_id_a,
            b.raw_note_id AS raw_note_id_b,
            (1 - (a.embedding <=> b.embedding))::FLOAT AS similarity_score,
            ROW_NUMBER() OVER (
                PARTITION BY a.id
                ORDER BY (a.embedding <=> b.embedding) DESC  -- 거리가 큰 것 우선 (낮은 유사도)
            ) AS rank
        FROM thought_units a,
        LATERAL (
            SELECT id, claim, embedding, raw_note_id
            FROM thought_units b_inner
            WHERE b_inner.id != a.id
              AND b_inner.raw_note_id != a.raw_note_id
              AND b_inner.embedding IS NOT NULL
            ORDER BY a.embedding <=> b_inner.embedding DESC  -- 낮은 유사도 우선 (정확도 우선)
            LIMIT top_k
        ) b
        WHERE a.embedding IS NOT NULL
    )
    SELECT DISTINCT
        LEAST(rp.thought_a_id, rp.thought_b_id)::INT AS thought_a_id,
        GREATEST(rp.thought_a_id, rp.thought_b_id)::INT AS thought_b_id,
        CASE
            WHEN rp.thought_a_id < rp.thought_b_id THEN rp.thought_a_claim
            ELSE rp.thought_b_claim
        END AS thought_a_claim,
        CASE
            WHEN rp.thought_a_id < rp.thought_b_id THEN rp.thought_b_claim
            ELSE rp.thought_a_claim
        END AS thought_b_claim,
        rp.similarity_score,
        CASE
            WHEN rp.thought_a_id < rp.thought_b_id THEN rp.raw_note_id_a
            ELSE rp.raw_note_id_b
        END AS raw_note_id_a,
        CASE
            WHEN rp.thought_a_id < rp.thought_b_id THEN rp.raw_note_id_b
            ELSE rp.raw_note_id_a
        END AS raw_note_id_b
    FROM ranked_pairs rp
    WHERE rp.rank <= top_k
    ORDER BY rp.similarity_score ASC  -- 낮은 유사도부터 반환 (Python 필터링 용이)
    LIMIT lim;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION find_similar_pairs_topk IS
'Accuracy-First Algorithm for low similarity pairs (v4)
- ORDER BY DESC to get farthest neighbors (low similarity = weak ties)
- Trade-off: Performance (no HNSW index) < Correctness (P10-P40 range)
- Returns low similarity first for Python filtering
- Complexity: O(n×K) but slower than v3 due to DESC ordering
- Optimizations: top_k=20 (reduced from 30), lim=10000 (reduced from 50000)
- Use case: Finding creative combinations between different ideas';

-- 성능 비교 테스트 쿼리
-- v3 (HNSW optimized, wrong data): ~4s, similarity 0.587-0.917
-- v4 (Accuracy first, correct data): ~60s?, similarity should be P10-P40 range

-- 실행 후 검증 쿼리
-- SELECT MIN(similarity_score), MAX(similarity_score), AVG(similarity_score), COUNT(*)
-- FROM find_similar_pairs_topk(0.0, 1.0, 20, 10000);
