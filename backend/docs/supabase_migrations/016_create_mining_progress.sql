-- ============================================================
-- 016: pair_mining_progress 테이블 생성
-- ============================================================
-- 목적: 마이닝 진행 상태 추적 및 재개 지원
-- ============================================================

CREATE TABLE IF NOT EXISTS pair_mining_progress (
    id SERIAL PRIMARY KEY,
    run_id UUID DEFAULT gen_random_uuid(),

    -- 진행 상태
    last_src_id INTEGER NOT NULL DEFAULT 0,
    total_src_processed INTEGER NOT NULL DEFAULT 0,
    total_pairs_inserted BIGINT NOT NULL DEFAULT 0,
    avg_candidates_per_src FLOAT,

    -- 파라미터 스냅샷 (재개 시 일관성 보장)
    src_batch INTEGER NOT NULL DEFAULT 30,
    dst_sample INTEGER NOT NULL DEFAULT 1200,
    k_per_src INTEGER NOT NULL DEFAULT 15,
    p_lo FLOAT NOT NULL DEFAULT 0.10,
    p_hi FLOAT NOT NULL DEFAULT 0.35,
    max_rounds INTEGER NOT NULL DEFAULT 3,
    seed INTEGER NOT NULL DEFAULT 42,

    -- 상태
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'in_progress', 'completed', 'paused', 'failed')),
    started_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    error_message TEXT
);

-- 싱글톤 보장: 하나의 활성 작업만 허용
CREATE UNIQUE INDEX IF NOT EXISTS idx_pmp_active
ON pair_mining_progress ((1))
WHERE status = 'in_progress';

-- 최신 진행 상태 조회용
CREATE INDEX IF NOT EXISTS idx_pmp_updated
ON pair_mining_progress (updated_at DESC);

-- 코멘트
COMMENT ON TABLE pair_mining_progress IS
'마이닝 진행 상태 추적. 키셋 페이징(last_src_id)으로 재개 가능.';

COMMENT ON COLUMN pair_mining_progress.last_src_id IS
'마지막 처리한 src thought ID. 다음 배치는 id > last_src_id로 시작.';

COMMENT ON COLUMN pair_mining_progress.avg_candidates_per_src IS
'src당 평균 후보 수. 품질 모니터링용.';
