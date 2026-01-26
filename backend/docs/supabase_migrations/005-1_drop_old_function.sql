-- Migration: 005-1_drop_old_function.sql
-- Purpose: 기존 find_similar_pairs_topk 함수 삭제 (반환 타입 변경 전 필요)
-- Created: 2026-01-26
-- Parent: 005_create_topk_function.sql
-- Reason: PostgreSQL은 함수의 반환 타입을 변경할 때 기존 함수를 먼저 삭제해야 함

-- ============================================================================
-- 주의사항
-- ============================================================================
-- 이 파일은 005_create_topk_function.sql 실행 **전에** 실행해야 합니다.
-- 순서:
--   1. 005-1_drop_old_function.sql (이 파일)
--   2. 005_create_topk_function.sql (새 함수 생성)

-- ============================================================================
-- 기존 함수 삭제
-- ============================================================================

DROP FUNCTION IF EXISTS find_similar_pairs_topk(double precision, double precision, integer, integer);

-- ============================================================================
-- 실행 확인
-- ============================================================================

-- 함수가 삭제되었는지 확인:
-- SELECT proname, proargtypes
-- FROM pg_proc
-- WHERE proname = 'find_similar_pairs_topk';
-- (결과가 없어야 함)
