-- Import Jobs Table for Background Task Tracking
-- Created: 2026-01-14
-- Purpose: Track Notion import jobs that run in background

CREATE TABLE IF NOT EXISTS import_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status TEXT NOT NULL CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    mode TEXT NOT NULL CHECK (mode IN ('database', 'parent_page')),
    total_pages INTEGER DEFAULT 0,
    processed_pages INTEGER DEFAULT 0,
    imported_pages INTEGER DEFAULT 0,
    skipped_pages INTEGER DEFAULT 0,
    error_message TEXT,
    failed_pages JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    config_json JSONB DEFAULT '{}'::jsonb
);

-- Index for status queries
CREATE INDEX IF NOT EXISTS idx_import_jobs_status ON import_jobs(status);

-- Index for recent jobs queries
CREATE INDEX IF NOT EXISTS idx_import_jobs_created_at ON import_jobs(created_at DESC);

-- Composite index for status + time filtering
CREATE INDEX IF NOT EXISTS idx_import_jobs_status_created ON import_jobs(status, created_at DESC);

-- Comments for documentation
COMMENT ON TABLE import_jobs IS 'Tracks background import jobs from Notion API';
COMMENT ON COLUMN import_jobs.status IS 'Job status: pending, processing, completed, failed';
COMMENT ON COLUMN import_jobs.mode IS 'Import mode: database (query_database) or parent_page (fetch_child_pages)';
COMMENT ON COLUMN import_jobs.failed_pages IS 'JSONB array of {page_id, error_message} for failed pages';
COMMENT ON COLUMN import_jobs.config_json IS 'Job configuration (page_size, filters, etc.)';
