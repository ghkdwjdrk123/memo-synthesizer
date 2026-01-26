-- =====================================================
-- Migration 007: thought_pairs 테이블 확장
-- =====================================================
-- 목적: Claude 평가 점수, 품질 등급, 에세이 콘텐츠 컬럼 추가
-- 작성일: 2026-01-26
-- 영향: 기존 데이터 무손실 (NULL 허용 컬럼)
-- =====================================================

-- =====================================================
-- 1. 컬럼 추가 (안전하게 IF NOT EXISTS 사용)
-- =====================================================

-- 1.1. claude_score: Claude LLM 평가 점수 (0-100)
ALTER TABLE thought_pairs
ADD COLUMN IF NOT EXISTS claude_score INTEGER;

-- 1.2. quality_tier: 품질 등급 (standard/premium/excellent)
ALTER TABLE thought_pairs
ADD COLUMN IF NOT EXISTS quality_tier TEXT DEFAULT 'standard';

-- 1.3. essay_content: 미리 생성된 에세이 내용 (선택적)
ALTER TABLE thought_pairs
ADD COLUMN IF NOT EXISTS essay_content TEXT;

-- =====================================================
-- 2. 제약조건 추가
-- =====================================================

-- 2.1. claude_score 범위 제약 (0-100, NULL 허용)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'claude_score_range'
    ) THEN
        ALTER TABLE thought_pairs
        ADD CONSTRAINT claude_score_range
        CHECK (claude_score IS NULL OR (claude_score >= 0 AND claude_score <= 100));
    END IF;
END
$$;

-- 2.2. quality_tier 값 제약 (3가지 등급만 허용)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'quality_tier_values'
    ) THEN
        ALTER TABLE thought_pairs
        ADD CONSTRAINT quality_tier_values
        CHECK (quality_tier IN ('standard', 'premium', 'excellent'));
    END IF;
END
$$;

-- =====================================================
-- 3. 인덱스 생성
-- =====================================================

-- 3.1. claude_score partial index (점수 있는 행만, DESC 정렬)
-- 용도: 고득점 페어 조회 최적화 (추천 시스템)
CREATE INDEX IF NOT EXISTS idx_thought_pairs_claude_score
ON thought_pairs(claude_score DESC)
WHERE claude_score IS NOT NULL;

-- 3.2. quality_tier + is_used_in_essay 복합 partial index
-- 용도: 미사용 고품질 페어 조회 최적화
CREATE INDEX IF NOT EXISTS idx_thought_pairs_quality_unused
ON thought_pairs(quality_tier, claude_score DESC)
WHERE is_used_in_essay = FALSE;

-- =====================================================
-- 4. 컬럼 설명 추가 (메타데이터)
-- =====================================================

COMMENT ON COLUMN thought_pairs.claude_score IS
'Claude LLM 평가 점수 (0-100). NULL = 미평가. 높을수록 논리적 확장 가능성이 높음';

COMMENT ON COLUMN thought_pairs.quality_tier IS
'품질 등급: standard(65-84점), premium(85-94점), excellent(95-100점)';

COMMENT ON COLUMN thought_pairs.essay_content IS
'선택적으로 미리 생성된 에세이 전문. UI 미리보기용. NULL 허용';

-- =====================================================
-- 5. 기존 데이터 영향 검증 쿼리 (실행 후 확인용)
-- =====================================================

-- 5.1. 전체 레코드 수 확인
-- SELECT COUNT(*) as total_pairs FROM thought_pairs;

-- 5.2. 신규 컬럼 NULL 상태 확인
-- SELECT
--     COUNT(*) as total,
--     COUNT(claude_score) as has_score,
--     COUNT(*) - COUNT(claude_score) as null_score,
--     COUNT(DISTINCT quality_tier) as distinct_tiers,
--     COUNT(essay_content) as has_content
-- FROM thought_pairs;

-- 5.3. 기존 제약조건 무결성 확인
-- SELECT
--     thought_a_id,
--     thought_b_id,
--     similarity_score,
--     claude_score,
--     quality_tier
-- FROM thought_pairs
-- WHERE
--     thought_a_id >= thought_b_id  -- 기존 제약 위반 확인
--     OR similarity_score NOT BETWEEN 0 AND 1  -- 기존 제약 위반 확인
--     OR (claude_score IS NOT NULL AND claude_score NOT BETWEEN 0 AND 100);  -- 신규 제약 위반 확인

-- 5.4. 인덱스 생성 확인
-- SELECT
--     indexname,
--     indexdef
-- FROM pg_indexes
-- WHERE tablename = 'thought_pairs'
-- ORDER BY indexname;

-- =====================================================
-- 6. 롤백 SQL (필요시 사용)
-- =====================================================

-- 주의: 이 SQL을 실행하면 추가된 컬럼과 데이터가 영구 삭제됩니다.
-- 운영 환경에서는 반드시 백업 후 실행하세요.

/*
-- 6.1. 인덱스 삭제
DROP INDEX IF EXISTS idx_thought_pairs_claude_score;
DROP INDEX IF EXISTS idx_thought_pairs_quality_unused;

-- 6.2. 제약조건 삭제
ALTER TABLE thought_pairs DROP CONSTRAINT IF EXISTS claude_score_range;
ALTER TABLE thought_pairs DROP CONSTRAINT IF EXISTS quality_tier_values;

-- 6.3. 컬럼 삭제
ALTER TABLE thought_pairs DROP COLUMN IF EXISTS essay_content;
ALTER TABLE thought_pairs DROP COLUMN IF EXISTS quality_tier;
ALTER TABLE thought_pairs DROP COLUMN IF EXISTS claude_score;

-- 6.4. 롤백 검증
SELECT
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_name = 'thought_pairs'
ORDER BY ordinal_position;
*/

-- =====================================================
-- 7. 마이그레이션 완료 로그
-- =====================================================

DO $$
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Migration 007 완료';
    RAISE NOTICE '추가된 컬럼: claude_score, quality_tier, essay_content';
    RAISE NOTICE '추가된 인덱스: idx_thought_pairs_claude_score, idx_thought_pairs_quality_unused';
    RAISE NOTICE '기존 데이터: 영향 없음 (모든 신규 컬럼 NULL 허용)';
    RAISE NOTICE '========================================';
END
$$;
