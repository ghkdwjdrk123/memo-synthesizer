-- ============================================================
-- 015: thought_units에 rand_key 컬럼 추가
-- ============================================================
-- 목적: 빠른 결정론적 샘플링 지원
-- ORDER BY random() 대신 rand_key 범위 조회로 인덱스 활용
-- ============================================================

-- 1. rand_key 컬럼 추가
ALTER TABLE thought_units
ADD COLUMN IF NOT EXISTS rand_key DOUBLE PRECISION DEFAULT random();

-- 2. 기존 레코드에 rand_key 값 채우기
UPDATE thought_units
SET rand_key = random()
WHERE rand_key IS NULL;

-- 3. NOT NULL 제약 추가
ALTER TABLE thought_units
ALTER COLUMN rand_key SET NOT NULL;

-- 4. 빠른 샘플링용 인덱스
CREATE INDEX IF NOT EXISTS idx_thought_units_rand_key
ON thought_units (rand_key);

-- 5. 코멘트
COMMENT ON COLUMN thought_units.rand_key IS
'결정론적 샘플링용 랜덤 키. seed 기반 범위 조회로 ORDER BY random() 대체.
사용법: WHERE rand_key >= (seed % 1000000) / 1000000.0 ORDER BY rand_key LIMIT n';

-- 6. 통계 갱신
ANALYZE thought_units;
