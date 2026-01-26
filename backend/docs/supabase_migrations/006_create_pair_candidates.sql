-- 006_create_pair_candidates.sql
-- 하이브리드 C 전략: 전체 후보 페어 보관 및 LLM 배치 평가 관리 테이블
-- 예상 규모: 초기 30,000개, 최대 100,000개까지 확장 가능

-- ============================================================================
-- 테이블 생성
-- ============================================================================

CREATE TABLE IF NOT EXISTS pair_candidates (
    -- 기본 키
    id BIGSERIAL PRIMARY KEY,

    -- 페어 정보 (thought_units 외래키)
    thought_a_id INTEGER NOT NULL REFERENCES thought_units(id) ON DELETE CASCADE,
    thought_b_id INTEGER NOT NULL REFERENCES thought_units(id) ON DELETE CASCADE,

    -- 유사도 (코사인 유사도: 1 - cosine_distance)
    similarity FLOAT NOT NULL CHECK (similarity >= 0.0 AND similarity <= 1.0),

    -- 출처 노트 추적 (다양성 필터용)
    raw_note_id_a UUID NOT NULL REFERENCES raw_notes(id) ON DELETE CASCADE,
    raw_note_id_b UUID NOT NULL REFERENCES raw_notes(id) ON DELETE CASCADE,

    -- LLM 평가 상태 관리
    llm_score INTEGER CHECK (llm_score IS NULL OR (llm_score >= 0 AND llm_score <= 100)),
    llm_status TEXT NOT NULL DEFAULT 'pending' CHECK (llm_status IN ('pending', 'processing', 'completed', 'failed')),
    llm_attempts INTEGER NOT NULL DEFAULT 0 CHECK (llm_attempts >= 0 AND llm_attempts <= 3),
    last_evaluated_at TIMESTAMPTZ,
    evaluation_error TEXT,

    -- 메타데이터
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 제약조건
    CONSTRAINT pc_different_thoughts CHECK (thought_a_id != thought_b_id),
    CONSTRAINT pc_ordered_pair CHECK (thought_a_id < thought_b_id),
    CONSTRAINT pc_unique_pair UNIQUE(thought_a_id, thought_b_id)
);

-- ============================================================================
-- 코멘트 추가 (테이블 및 주요 컬럼)
-- ============================================================================

COMMENT ON TABLE pair_candidates IS
'하이브리드 C 전략: 전체 후보 페어 보관 및 LLM 배치 평가 관리. 초기 30K, 최대 100K 페어 수용 가능.';

COMMENT ON COLUMN pair_candidates.id IS
'페어 후보 고유 ID (BIGSERIAL, 최대 9.2 quintillion)';

COMMENT ON COLUMN pair_candidates.similarity IS
'코사인 유사도 (1 - cosine_distance), 범위: 0.05-0.35 (약한 연결 선호)';

COMMENT ON COLUMN pair_candidates.raw_note_id_a IS
'사고 A의 출처 노트 ID (다양성 필터용)';

COMMENT ON COLUMN pair_candidates.raw_note_id_b IS
'사고 B의 출처 노트 ID (다양성 필터용)';

COMMENT ON COLUMN pair_candidates.llm_score IS
'Claude 평가 점수 (0-100), NULL = 미평가';

COMMENT ON COLUMN pair_candidates.llm_status IS
'LLM 평가 상태: pending(대기) | processing(처리중) | completed(완료) | failed(실패)';

COMMENT ON COLUMN pair_candidates.llm_attempts IS
'재시도 횟수 (0-3), 3회 초과 시 영구 실패 처리';

-- ============================================================================
-- 인덱스 생성 (성능 최적화)
-- ============================================================================

-- 1. 배치 워커용 인덱스 (pending 상태만 partial index로 관리)
-- 용도: 배치 워커가 미평가 페어 100개를 빠르게 조회
-- 예상 성능: 30K 중 100개 조회 < 100ms
CREATE INDEX idx_pc_pending_batch ON pair_candidates (created_at)
WHERE llm_status = 'pending';

COMMENT ON INDEX idx_pc_pending_batch IS
'배치 워커용 partial index: pending 상태 페어만 인덱싱 (FIFO 처리)';

-- 2. 추천용 고득점 인덱스 (completed 상태의 high-score 조회)
-- 용도: 프론트엔드가 상위 20개 페어를 빠르게 추천
-- 예상 성능: 30K 중 20개 조회 < 50ms
CREATE INDEX idx_pc_high_score ON pair_candidates (llm_score DESC)
WHERE llm_status = 'completed' AND llm_score IS NOT NULL;

COMMENT ON INDEX idx_pc_high_score IS
'추천용 partial index: completed 상태의 고득점 페어 (DESC 정렬)';

-- 3. 다양성 필터용 출처 조합 인덱스
-- 용도: 동일 출처 페어 제외 (같은 노트 내 사고 조합 방지)
-- 예상 성능: 출처 기반 필터링 < 100ms
CREATE INDEX idx_pc_source_diversity ON pair_candidates (raw_note_id_a, raw_note_id_b);

COMMENT ON INDEX idx_pc_source_diversity IS
'다양성 필터용: 출처 노트 조합 기반 중복 방지';

-- 4. 유사도 범위 조회 인덱스
-- 용도: 특정 유사도 범위 (예: 0.05-0.35) 내 페어 조회
-- 예상 성능: 범위 쿼리 < 200ms
CREATE INDEX idx_pc_similarity_range ON pair_candidates (similarity);

COMMENT ON INDEX idx_pc_similarity_range IS
'유사도 범위 조회용: 약한 연결 (0.05-0.35) 필터링';

-- ============================================================================
-- 통계 갱신 (쿼리 플래너 최적화)
-- ============================================================================

ANALYZE pair_candidates;

-- ============================================================================
-- 예상 쿼리 패턴 (참고용)
-- ============================================================================

-- Q1: 배치 워커가 미평가 페어 100개 가져오기
-- SELECT id, thought_a_id, thought_b_id, similarity
-- FROM pair_candidates
-- WHERE llm_status = 'pending'
-- ORDER BY created_at
-- LIMIT 100;

-- Q2: 프론트엔드가 고득점 페어 20개 추천
-- SELECT id, thought_a_id, thought_b_id, similarity, llm_score
-- FROM pair_candidates
-- WHERE llm_status = 'completed' AND llm_score >= 70
-- ORDER BY llm_score DESC
-- LIMIT 20;

-- Q3: 다양성 필터 (동일 출처 제외)
-- SELECT *
-- FROM pair_candidates
-- WHERE llm_status = 'completed'
--   AND raw_note_id_a != raw_note_id_b
-- ORDER BY llm_score DESC;

-- Q4: 유사도 범위 내 미평가 페어
-- SELECT COUNT(*)
-- FROM pair_candidates
-- WHERE similarity BETWEEN 0.05 AND 0.35
--   AND llm_status = 'pending';

-- ============================================================================
-- 성능 벤치마크 목표
-- ============================================================================

-- 데이터 규모: 30,000개 페어 (초기), 최대 100,000개
--
-- INSERT 성능: 30,000개 배치 삽입 < 3분
-- SELECT 성능:
--   - 배치 조회 (100개): < 100ms
--   - 고득점 조회 (20개): < 50ms
--   - 다양성 필터: < 100ms
--   - 유사도 범위: < 200ms
