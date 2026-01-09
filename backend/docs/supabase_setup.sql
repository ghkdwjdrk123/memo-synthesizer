-- Memo Synthesizer Database Setup
-- Run this SQL in Supabase SQL Editor

-- 1. Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. RAW 레이어: Notion 원본
CREATE TABLE IF NOT EXISTS raw_notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    notion_page_id TEXT UNIQUE NOT NULL,
    notion_url TEXT NOT NULL,
    title TEXT,
    content TEXT,
    properties_json JSONB DEFAULT '{}'::jsonb,
    notion_created_time TIMESTAMPTZ NOT NULL,
    notion_last_edited_time TIMESTAMPTZ NOT NULL,
    imported_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_raw_notes_notion_page_id ON raw_notes(notion_page_id);
COMMENT ON TABLE raw_notes IS 'Notion에서 가져온 원본 메모 (MVP+1부터 암호화)';

-- 3. NORMALIZED 레이어: 사고 단위 + 임베딩
CREATE TABLE IF NOT EXISTS thought_units (
    id SERIAL PRIMARY KEY,
    raw_note_id UUID NOT NULL REFERENCES raw_notes(id) ON DELETE CASCADE,
    claim TEXT NOT NULL,
    context TEXT,
    embedding vector(1536),
    embedding_model TEXT DEFAULT 'text-embedding-3-small',
    extracted_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_thought_units_raw_note ON thought_units(raw_note_id);
COMMENT ON TABLE thought_units IS '메모에서 추출된 사고 단위 (평문 유지 - pgvector 검색 위해)';

-- pgvector 유사도 검색 인덱스 (데이터 1000개 이상일 때 생성 권장)
-- CREATE INDEX idx_thought_units_embedding ON thought_units
-- USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- 4. ZK 레이어: 선택된 페어
CREATE TABLE IF NOT EXISTS thought_pairs (
    id SERIAL PRIMARY KEY,
    thought_a_id INTEGER NOT NULL REFERENCES thought_units(id) ON DELETE CASCADE,
    thought_b_id INTEGER NOT NULL REFERENCES thought_units(id) ON DELETE CASCADE,
    similarity_score FLOAT NOT NULL CHECK (similarity_score >= 0 AND similarity_score <= 1),
    connection_reason TEXT,
    selected_at TIMESTAMPTZ DEFAULT NOW(),
    is_used_in_essay BOOLEAN DEFAULT FALSE,
    CONSTRAINT different_thoughts CHECK (thought_a_id != thought_b_id),
    CONSTRAINT ordered_pair CHECK (thought_a_id < thought_b_id),
    UNIQUE(thought_a_id, thought_b_id)
);
CREATE INDEX IF NOT EXISTS idx_thought_pairs_unused ON thought_pairs(is_used_in_essay)
WHERE is_used_in_essay = FALSE;
COMMENT ON TABLE thought_pairs IS '논리적 확장 가능한 사고 단위 페어';

-- 5. Essay 결과물
CREATE TABLE IF NOT EXISTS essays (
    id SERIAL PRIMARY KEY,
    type TEXT DEFAULT 'essay',
    title TEXT NOT NULL,
    outline JSONB NOT NULL,
    used_thoughts_json JSONB NOT NULL,
    reason TEXT NOT NULL,
    pair_id INTEGER NOT NULL REFERENCES thought_pairs(id) ON DELETE CASCADE,
    generated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_essays_generated_at ON essays(generated_at DESC);
COMMENT ON TABLE essays IS '최종 생성된 글감 결과물';

-- 6. 처리 상태 추적 (LLM 실패 대응 및 재시도)
CREATE TABLE IF NOT EXISTS processing_status (
    id SERIAL PRIMARY KEY,
    raw_note_id UUID NOT NULL REFERENCES raw_notes(id) ON DELETE CASCADE,
    step TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(raw_note_id, step)
);
CREATE INDEX IF NOT EXISTS idx_processing_status_step_status ON processing_status(step, status);
CREATE INDEX IF NOT EXISTS idx_processing_status_retry ON processing_status(status, retry_count)
WHERE status = 'failed' AND retry_count < 3;
COMMENT ON TABLE processing_status IS 'LLM 처리 상태 추적 및 재시도 관리';

-- 7. Stored Procedure for Step 3: similarity search (수정 버전 - 낮은 유사도 + 동일 출처 제외)
CREATE OR REPLACE FUNCTION find_similar_pairs(
    min_sim FLOAT DEFAULT 0.05,  -- 기본값 변경: 0.3 → 0.05 (낮은 유사도 = 서로 다른 아이디어)
    max_sim FLOAT DEFAULT 0.35,  -- 기본값 변경: 0.7 → 0.35
    lim INT DEFAULT 20
)
RETURNS TABLE (
    thought_a_id INT,
    thought_b_id INT,
    thought_a_claim TEXT,
    thought_b_claim TEXT,
    similarity_score FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        a.id::INT as thought_a_id,
        b.id::INT as thought_b_id,
        a.claim as thought_a_claim,
        b.claim as thought_b_claim,
        (1 - (a.embedding <=> b.embedding))::FLOAT as similarity_score
    FROM thought_units a
    JOIN thought_units b ON a.id < b.id
    WHERE a.embedding IS NOT NULL
      AND b.embedding IS NOT NULL
      AND a.raw_note_id != b.raw_note_id  -- ⭐ 추가: 동일 출처 제외 (서로 다른 메모에서만 연결)
      AND (1 - (a.embedding <=> b.embedding)) BETWEEN min_sim AND max_sim
    ORDER BY similarity_score DESC
    LIMIT lim;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION find_similar_pairs IS 'Step 3: Find thought unit pairs from DIFFERENT sources within low similarity range (weak ties)';

-- 완료 메시지
DO $$
BEGIN
    RAISE NOTICE 'Database setup completed successfully!';
    RAISE NOTICE 'Tables created: raw_notes, thought_units, thought_pairs, essays, processing_status';
    RAISE NOTICE 'Stored Procedure created: find_similar_pairs()';
    RAISE NOTICE 'Next: Create pgvector index after inserting 1000+ thought_units';
END $$;
