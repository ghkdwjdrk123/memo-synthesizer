"""
Supabase 데이터베이스 초기 설정 스크립트.

supabase_setup.sql의 내용을 실행합니다.
"""

import asyncio
import sys
from pathlib import Path

from supabase import create_async_client
from config import settings


async def setup_database():
    """데이터베이스 테이블 생성."""
    print("🔧 Setting up Supabase database...")

    # SQL 파일 읽기
    sql_file = Path(__file__).parent / "supabase_setup.sql"
    if not sql_file.exists():
        print(f"❌ SQL file not found: {sql_file}")
        sys.exit(1)

    sql_content = sql_file.read_text()
    print(f"📄 Read SQL file: {sql_file.name}")

    # Supabase 클라이언트 생성
    try:
        client = await create_async_client(
            settings.supabase_url,
            settings.supabase_key
        )
        print(f"✅ Connected to Supabase: {settings.supabase_url}")
    except Exception as e:
        print(f"❌ Failed to connect to Supabase: {e}")
        sys.exit(1)

    # SQL 문을 개별적으로 실행
    # (DO $$ 블록과 CREATE 문을 분리)
    statements = [
        # pgvector extension
        "CREATE EXTENSION IF NOT EXISTS vector;",

        # raw_notes 테이블
        """
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
        """,
        "CREATE INDEX IF NOT EXISTS idx_raw_notes_notion_page_id ON raw_notes(notion_page_id);",

        # thought_units 테이블
        """
        CREATE TABLE IF NOT EXISTS thought_units (
            id SERIAL PRIMARY KEY,
            raw_note_id UUID NOT NULL REFERENCES raw_notes(id) ON DELETE CASCADE,
            claim TEXT NOT NULL,
            context TEXT,
            embedding vector(1536),
            embedding_model TEXT DEFAULT 'text-embedding-3-small',
            extracted_at TIMESTAMPTZ DEFAULT NOW()
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_thought_units_raw_note ON thought_units(raw_note_id);",

        # thought_pairs 테이블
        """
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
        """,
        "CREATE INDEX IF NOT EXISTS idx_thought_pairs_unused ON thought_pairs(is_used_in_essay) WHERE is_used_in_essay = FALSE;",

        # essays 테이블
        """
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
        """,
        "CREATE INDEX IF NOT EXISTS idx_essays_generated_at ON essays(generated_at DESC);",

        # processing_status 테이블
        """
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
        """,
        "CREATE INDEX IF NOT EXISTS idx_processing_status_step_status ON processing_status(step, status);",
        "CREATE INDEX IF NOT EXISTS idx_processing_status_retry ON processing_status(status, retry_count) WHERE status = 'failed' AND retry_count < 3;",
    ]

    # 각 SQL 문 실행
    for i, stmt in enumerate(statements, 1):
        stmt = stmt.strip()
        if not stmt:
            continue

        try:
            # Supabase의 PostgREST는 직접 SQL 실행을 지원하지 않으므로
            # 수동으로 Supabase SQL Editor에서 실행해야 합니다
            print(f"⚠️  SQL statement {i}/{len(statements)} needs manual execution in Supabase SQL Editor")
            print(f"   Statement: {stmt[:60]}...")
        except Exception as e:
            print(f"❌ Failed to execute statement {i}: {e}")
            print(f"   Statement: {stmt[:100]}...")

    print("\n" + "="*70)
    print("⚠️  MANUAL ACTION REQUIRED:")
    print("="*70)
    print("Supabase Python client doesn't support direct SQL execution.")
    print("Please run the SQL file manually:")
    print()
    print("1. Go to: https://supabase.com/dashboard/project/zqrbrddmwrpogabizton/sql")
    print(f"2. Copy contents from: {sql_file}")
    print("3. Paste and run in SQL Editor")
    print()
    print("Or use the Supabase CLI:")
    print(f"   supabase db execute --file {sql_file}")
    print("="*70)


if __name__ == "__main__":
    asyncio.run(setup_database())
