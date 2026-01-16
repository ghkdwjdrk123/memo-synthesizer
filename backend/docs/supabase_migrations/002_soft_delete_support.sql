-- Soft Delete Support for raw_notes
-- Created: 2026-01-16
-- Purpose: 노션에서 삭제된 페이지를 논리적으로만 표시하여 Essay 보존
--
-- 사용 방법:
-- 1. Supabase Dashboard → SQL Editor
-- 2. 이 파일 내용 전체 복사 → Run
-- 3. 확인: SELECT is_deleted, deleted_at FROM raw_notes LIMIT 1;

-- Step 1: raw_notes 테이블에 soft delete 컬럼 추가
ALTER TABLE raw_notes
ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

-- Step 2: 성능 최적화 - Partial Index 생성
-- 활성 페이지 조회 최적화 (대부분의 쿼리는 is_deleted=FALSE 조회)
CREATE INDEX IF NOT EXISTS idx_raw_notes_active
ON raw_notes(notion_page_id)
WHERE is_deleted = FALSE;

-- 삭제된 페이지 조회 최적화 (관리자 기능용)
CREATE INDEX IF NOT EXISTS idx_raw_notes_deleted
ON raw_notes(deleted_at DESC)
WHERE is_deleted = TRUE;

-- Step 3: 기존 데이터 마이그레이션 (모든 기존 데이터는 is_deleted=FALSE)
UPDATE raw_notes
SET is_deleted = FALSE
WHERE is_deleted IS NULL;

-- 테이블 설명 업데이트
COMMENT ON COLUMN raw_notes.is_deleted IS
'논리적 삭제 플래그. TRUE면 Notion에서 삭제되었지만 DB에는 보존됨 (Essay 보호).';

COMMENT ON COLUMN raw_notes.deleted_at IS
'페이지가 삭제된 시점. is_deleted=TRUE일 때만 값 존재.';

COMMENT ON INDEX idx_raw_notes_active IS
'활성 페이지 조회 최적화. 대부분의 쿼리는 is_deleted=FALSE를 필터링함.';

COMMENT ON INDEX idx_raw_notes_deleted IS
'삭제된 페이지 히스토리 조회 최적화. 관리자 기능 및 복구 작업용.';

-- 검증 쿼리
-- 1. 컬럼 추가 확인
-- SELECT column_name, data_type, is_nullable, column_default
-- FROM information_schema.columns
-- WHERE table_name = 'raw_notes' AND column_name IN ('is_deleted', 'deleted_at');

-- 2. 인덱스 생성 확인
-- SELECT indexname, indexdef
-- FROM pg_indexes
-- WHERE tablename = 'raw_notes' AND indexname LIKE '%deleted%';

-- 3. 기존 데이터 확인
-- SELECT COUNT(*) as total_pages,
--        SUM(CASE WHEN is_deleted = FALSE THEN 1 ELSE 0 END) as active_pages,
--        SUM(CASE WHEN is_deleted = TRUE THEN 1 ELSE 0 END) as deleted_pages
-- FROM raw_notes;
