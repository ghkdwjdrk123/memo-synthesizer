-- Migration: 005_create_topk_function.sql
-- Purpose: Top-K 알고리즘으로 O(n²) → O(n×K) 복잡도 개선
-- Created: 2025-01-22
-- Updated: 2026-01-26 - 하이브리드 전략을 위해 raw_note_id_a, raw_note_id_b 추가

-- Top-K 방식 유사도 검색 함수
CREATE OR REPLACE FUNCTION find_similar_pairs_topk(
    min_sim FLOAT DEFAULT 0.05,
    max_sim FLOAT DEFAULT 0.35,
    top_k INT DEFAULT 30,
    lim INT DEFAULT 20
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
                ORDER BY (a.embedding <=> b.embedding) ASC
            ) AS rank
        FROM thought_units a
        CROSS JOIN LATERAL (
            SELECT id, claim, embedding, raw_note_id
            FROM thought_units
            WHERE id != a.id
              AND raw_note_id != a.raw_note_id
              AND embedding IS NOT NULL
            ORDER BY embedding <=> a.embedding
            LIMIT top_k
        ) b
        WHERE (1 - (a.embedding <=> b.embedding)) BETWEEN min_sim AND max_sim
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
    ORDER BY rp.similarity_score DESC
    LIMIT lim;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION find_similar_pairs_topk IS
'Step 3 (Top-K Algorithm): Find thought pairs using HNSW index with raw_note_id
- Complexity: O(n²) → O(n×K) (98% reduction)
- Performance: 60s+ timeout → 5s
- Each thought searches only top K similar candidates
- HNSW index automatically used via ORDER BY embedding <=> query
- Returns raw_note_id_a and raw_note_id_b for hybrid strategy (v2)';

-- 성능 테스트 쿼리 (실행 시간 확인용)
-- EXPLAIN (ANALYZE, BUFFERS)
-- SELECT * FROM find_similar_pairs_topk(0.05, 0.35, 30, 20);