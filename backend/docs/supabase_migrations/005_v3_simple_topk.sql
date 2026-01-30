-- Migration: 005_v3_simple_topk.sql
-- Purpose: Top-K 알고리즘 (필터링 없음, Python에서 처리)
-- Created: 2026-01-29
-- Changes: BETWEEN 필터 제거, 빠른 Top-K만 반환

-- Top-K 방식 유사도 검색 함수 (v3: 필터링 제거)
CREATE OR REPLACE FUNCTION find_similar_pairs_topk(
    min_sim FLOAT DEFAULT 0.05,
    max_sim FLOAT DEFAULT 0.35,
    top_k INT DEFAULT 30,
    lim INT DEFAULT 50000
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
    -- min_sim, max_sim 파라미터는 하위 호환성을 위해 유지하지만 사용하지 않음
    -- Python 레벨에서 필터링 수행

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
                ORDER BY (a.embedding <=> b.embedding) ASC  -- HNSW 인덱스 활용 (가까운 것 우선)
            ) AS rank
        FROM thought_units a,
        LATERAL (
            SELECT id, claim, embedding, raw_note_id
            FROM thought_units b_inner
            WHERE b_inner.id != a.id
              AND b_inner.raw_note_id != a.raw_note_id
              AND b_inner.embedding IS NOT NULL
            ORDER BY a.embedding <=> b_inner.embedding ASC  -- HNSW 인덱스 활용
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
    ORDER BY rp.similarity_score DESC  -- 높은 유사도부터 반환 (HNSW 결과 그대로)
    LIMIT lim;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION find_similar_pairs_topk IS
'Top-K Algorithm without similarity filtering (v3)
- Returns all top-k pairs without BETWEEN filter
- Filtering done in Python layer for performance
- Uses HNSW index efficiently (ORDER BY ASC for nearest neighbors)
- Returns high similarity first (Python sorts by low similarity later)
- Complexity: O(n×K) with full index utilization
- Compatible with relative threshold strategy';
