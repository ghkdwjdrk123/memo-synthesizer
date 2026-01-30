-- 008: 유사도 분포 캐시 테이블 생성
-- 목적: 백분위수 사전 계산 및 캐싱 (상대적 임계값 전략)
-- 날짜: 2026-01-26

CREATE TABLE IF NOT EXISTS similarity_distribution_cache (
    id SERIAL PRIMARY KEY,

    -- 분포 통계
    thought_unit_count INTEGER NOT NULL,
    total_pair_count BIGINT NOT NULL,

    -- 백분위수 (Percentile) 저장
    p0 FLOAT NOT NULL,   -- 최소값
    p10 FLOAT NOT NULL,
    p20 FLOAT NOT NULL,
    p30 FLOAT NOT NULL,
    p40 FLOAT NOT NULL,
    p50 FLOAT NOT NULL,  -- 중간값 (median)
    p60 FLOAT NOT NULL,
    p70 FLOAT NOT NULL,
    p80 FLOAT NOT NULL,
    p90 FLOAT NOT NULL,
    p100 FLOAT NOT NULL, -- 최대값

    -- 평균 및 표준편차
    mean FLOAT NOT NULL,
    stddev FLOAT NOT NULL,

    -- 캐시 메타데이터
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    embedding_model TEXT NOT NULL DEFAULT 'text-embedding-3-small',
    calculation_duration_ms INTEGER,

    CONSTRAINT single_cache_row UNIQUE (id)
);

-- 초기 레코드 생성 (빈 값)
INSERT INTO similarity_distribution_cache (
    id, thought_unit_count, total_pair_count,
    p0, p10, p20, p30, p40, p50, p60, p70, p80, p90, p100,
    mean, stddev
) VALUES (
    1, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0
) ON CONFLICT (id) DO NOTHING;

-- 설명
COMMENT ON TABLE similarity_distribution_cache IS '유사도 분포 캐시: 백분위수 사전 계산 (24시간 TTL)';
COMMENT ON COLUMN similarity_distribution_cache.p10 IS 'P10-P40 전략의 시작점 (창의적 조합)';
COMMENT ON COLUMN similarity_distribution_cache.p40 IS 'P10-P40 전략의 종료점 (창의적 조합)';
