-- Migration: 004_create_hnsw_index.sql
-- Purpose: HNSW 인덱스로 벡터 검색 성능 개선 (60초+ -> 5초 이하)
-- Created: 2025-01-22

-- 기존 IVFFlat 인덱스 제거 (있다면)
DROP INDEX IF EXISTS idx_thought_units_embedding;

-- HNSW 인덱스 생성
-- m=16: 각 노드당 최대 연결 수 (기본값, 1K~10K 벡터에 최적)
-- ef_construction=200: 인덱스 구축 시 탐색 범위 (99%+ 정확도)
CREATE INDEX idx_thought_units_embedding_hnsw
ON thought_units
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 200);

-- 통계 업데이트 (쿼리 플래너 최적화)
ANALYZE thought_units;

-- 인덱스 생성 확인
SELECT
    indexname,
    indexdef,
    pg_size_pretty(pg_relation_size(indexname::regclass)) as size
FROM pg_indexes
WHERE tablename = 'thought_units'
AND indexname LIKE '%embedding%';
