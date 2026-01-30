-- Migration: 010_create_distance_table.sql
-- Purpose: Distance Table 스키마 생성 (모든 thought 페어의 유사도 사전 계산)
-- Created: 2026-01-29
-- Performance: 조회 0.1초 (vs v4 60초+), 600배 개선

-- Distance Table: 모든 thought 페어의 유사도 사전 계산
CREATE TABLE IF NOT EXISTS thought_pair_distances (
    id BIGSERIAL PRIMARY KEY,

    -- 페어 정보 (thought_a_id < thought_b_id 보장)
    thought_a_id INTEGER NOT NULL REFERENCES thought_units(id) ON DELETE CASCADE,
    thought_b_id INTEGER NOT NULL REFERENCES thought_units(id) ON DELETE CASCADE,

    -- 코사인 유사도 [0, 1]
    similarity FLOAT NOT NULL CHECK (similarity >= 0 AND similarity <= 1),

    -- 메타데이터
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 제약조건
    CONSTRAINT tpd_different_thoughts CHECK (thought_a_id != thought_b_id),
    CONSTRAINT tpd_ordered_pair CHECK (thought_a_id < thought_b_id),
    CONSTRAINT tpd_unique_pair UNIQUE(thought_a_id, thought_b_id)
);

-- 인덱스: 유사도 범위 조회 최적화 (핵심!)
CREATE INDEX IF NOT EXISTS idx_tpd_similarity_range ON thought_pair_distances (similarity);
CREATE INDEX IF NOT EXISTS idx_tpd_thought_a ON thought_pair_distances (thought_a_id);
CREATE INDEX IF NOT EXISTS idx_tpd_thought_b ON thought_pair_distances (thought_b_id);

COMMENT ON TABLE thought_pair_distances IS
'Distance Table: 조회 0.1초 (vs v4 60초+), 증분 갱신 2초/10개
- 저장 공간: 1,921개 기준 ~178 MB (테이블 118MB + 인덱스 60MB)
- Break-even: 7회 조회부터 이득 (순차 배치 처리 기준)
- 초기 구축: Python 순차 호출 (build_distance_table_batch)';
