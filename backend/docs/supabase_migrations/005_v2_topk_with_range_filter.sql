-- Migration: 005_v2_topk_with_range_filter.sql
-- Purpose: Top-K 알고리즘 + 유사도 범위 사전 필터링
-- Created: 2026-01-29
-- Changes: LATERAL JOIN 내부에서 유사도 범위 필터링 (상대적 임계값 대응)

-- Top-K 방식 유사도 검색 함수 (v2: 범위 필터링 개선)
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
                ORDER BY (a.embedding <=> b.embedding) DESC  -- 거리가 큰 것 우선 (낮은 유사도)
            ) AS rank
        FROM thought_units a,
        LATERAL (
            SELECT id, claim, embedding, raw_note_id
            FROM thought_units b_inner
            WHERE b_inner.id != a.id
              AND b_inner.raw_note_id != a.raw_note_id
              AND b_inner.embedding IS NOT NULL
              -- 유사도 범위 사전 필터링 (핵심 개선)
              AND (1 - (a.embedding <=> b_inner.embedding)) BETWEEN min_sim AND max_sim
            ORDER BY a.embedding <=> b_inner.embedding DESC  -- 거리가 큰 것 우선
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
    ORDER BY rp.similarity_score ASC  -- 낮은 유사도부터 반환
    LIMIT lim;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION find_similar_pairs_topk IS
'Top-K Algorithm with similarity range pre-filtering (v2)
- LATERAL JOIN 내부에서 유사도 범위 필터링 (min_sim, max_sim)
- 낮은 유사도 우선 정렬 (ORDER BY DESC on distance)
- Complexity: O(n×K) with early filtering
- Supports relative threshold strategy (P10-P40, P30-P60 etc.)
- Returns raw_note_id_a and raw_note_id_b for hybrid strategy';
