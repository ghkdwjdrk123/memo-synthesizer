-- Migration: 006-1_add_connection_reason_to_pair_candidates.sql
-- Purpose: pair_candidates 테이블에 connection_reason 컬럼 추가
-- Created: 2026-01-26
-- Parent: 006_create_pair_candidates.sql
-- Reason: BatchEvaluationWorker가 Claude 평가 결과를 저장할 때 필요 (006 테이블에 누락된 컬럼)

-- ============================================================================
-- 컬럼 추가
-- ============================================================================

ALTER TABLE pair_candidates
ADD COLUMN IF NOT EXISTS connection_reason TEXT;

-- ============================================================================
-- 코멘트 추가
-- ============================================================================

COMMENT ON COLUMN pair_candidates.connection_reason IS
'Claude가 생성한 두 사고 간 연결 이유 설명 (논리적 확장 가능성 근거)';

-- ============================================================================
-- 롤백 쿼리 (필요 시)
-- ============================================================================

-- ALTER TABLE pair_candidates DROP COLUMN IF EXISTS connection_reason;
