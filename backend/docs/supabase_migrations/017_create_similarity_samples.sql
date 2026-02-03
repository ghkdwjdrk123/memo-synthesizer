-- ============================================================
-- 017: similarity_samples 테이블 생성 (전역 분포 스케치용)
-- ============================================================
-- 목적: 전쌍 계산 없이 전역 분포를 근사하기 위한 샘플 저장
-- 방식: Raw sample 저장 → PERCENTILE_CONT로 p0-p100 계산
-- ============================================================

CREATE TABLE IF NOT EXISTS similarity_samples (
    id BIGSERIAL PRIMARY KEY,

    -- 실행 식별자
    run_id UUID NOT NULL,

    -- 샘플 데이터
    similarity FLOAT NOT NULL CHECK (similarity >= 0 AND similarity <= 1),

    -- 디버깅/분석용 (선택적)
    src_id INTEGER,
    dst_id INTEGER,

    -- 샘플링 메타데이터
    seed INTEGER,
    policy TEXT DEFAULT 'random_pairs',

    -- 타임스탬프
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 최신 run의 샘플 조회용
CREATE INDEX IF NOT EXISTS idx_ss_run_id
ON similarity_samples (run_id);

-- 시간순 정렬용
CREATE INDEX IF NOT EXISTS idx_ss_created_at
ON similarity_samples (created_at DESC);

-- 오래된 샘플 정리용 인덱스 (제거됨)
-- 참고: NOW()는 IMMUTABLE이 아니라 partial index에 사용 불가
-- 정리는 스케줄 작업으로 처리: DELETE FROM similarity_samples WHERE created_at < NOW() - INTERVAL '7 days';

-- 코멘트
COMMENT ON TABLE similarity_samples IS
'전역 분포 스케치용 유사도 샘플.
- build_distribution_sketch()로 샘플 수집
- calculate_distribution_from_sketch()로 p0-p100 계산
- 10만개 샘플 권장 (정확도 99%+)
- 7일 이상 오래된 샘플은 정리 권장';

COMMENT ON COLUMN similarity_samples.run_id IS
'샘플링 실행 ID. 같은 run의 샘플끼리 그룹화.';

COMMENT ON COLUMN similarity_samples.policy IS
'샘플링 정책. random_pairs(기본), stratified, etc.';
