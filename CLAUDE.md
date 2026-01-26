# 노션 기반 아이디어 조합 서비스 MVP

## Project Overview

노션 메모에서 사고 단위를 추출하고, 약한 연결을 찾아 글감을 생성하는 서비스

**파이프라인:** RAW → NORMALIZED → ZK → Essay

## Language

Always all output must write in Korean.

---
language: korean
---

# 🚨 최우선 규칙

| 규칙 | 설정 |
|-----|------|
| **출력 언어** | 한국어 (예외 없음) |
| **코드 주석** | 한국어 |
| **커밋 메시지** | 한국어 |

> ⚠️ 이 규칙은 /compact, 세션 재개 등 모든 상황에서 유지됩니다.


## Tech Stack

- **Backend:** FastAPI, Python 3.11+
- **Frontend:** Next.js 14 (App Router), TypeScript, Tailwind CSS
- **Database:** Supabase (PostgreSQL + pgvector)
- **LLM:** Claude 3.5 Sonnet (Anthropic), text-embedding-3-small (OpenAI)
- **External API:** Notion API

## Directory Structure

```
backend/
├── main.py
├── config.py
├── services/
│   ├── supabase_service.py   # DB CRUD + pgvector
│   ├── ai_service.py         # LLM calls
│   ├── notion_service.py     # Notion API
│   └── rate_limiter.py       # API rate limiting
├── routers/
│   ├── pipeline.py           # Pipeline endpoints
│   └── essays.py             # Essay CRUD
├── schemas/
│   ├── raw.py                # RawNote models
│   ├── normalized.py         # ThoughtUnit models
│   ├── zk.py                 # ThoughtPair models
│   ├── essay.py              # Essay models
│   └── processing.py         # ProcessingStatus
├── utils/
│   ├── validators.py         # JSON parsing
│   └── error_handlers.py     # Exception handling
└── tests/
    ├── conftest.py
    ├── unit/
    └── integration/

frontend/
├── src/
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx          # Main dashboard
│   │   └── login/page.tsx    # Login page
│   ├── components/
│   │   ├── EssayCard.tsx
│   │   ├── PipelineControl.tsx
│   │   └── DatabaseSelector.tsx
│   ├── lib/
│   │   ├── api.ts            # Backend client
│   │   └── types.ts          # TypeScript types
│   └── hooks/
│       ├── usePipeline.ts
│       └── useAuth.ts
└── __tests__/
```

## Database Schema

```sql
-- 1. RAW 레이어: Notion 원본
CREATE TABLE raw_notes (
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
CREATE INDEX idx_raw_notes_notion_page_id ON raw_notes(notion_page_id);

-- 2. NORMALIZED 레이어: 사고 단위 + 임베딩
CREATE TABLE thought_units (
    id SERIAL PRIMARY KEY,
    raw_note_id UUID NOT NULL REFERENCES raw_notes(id) ON DELETE CASCADE,
    claim TEXT NOT NULL,
    context TEXT,
    embedding vector(1536),  -- OpenAI text-embedding-3-small
    embedding_model TEXT DEFAULT 'text-embedding-3-small',
    extracted_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_thought_units_raw_note ON thought_units(raw_note_id);
CREATE INDEX idx_thought_units_embedding ON thought_units
USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- 3. ZK 레이어: 선택된 페어
CREATE TABLE thought_pairs (
    id SERIAL PRIMARY KEY,
    thought_a_id INTEGER NOT NULL REFERENCES thought_units(id),
    thought_b_id INTEGER NOT NULL REFERENCES thought_units(id),
    similarity_score FLOAT NOT NULL CHECK (similarity_score >= 0 AND similarity_score <= 1),
    connection_reason TEXT,
    selected_at TIMESTAMPTZ DEFAULT NOW(),
    is_used_in_essay BOOLEAN DEFAULT FALSE,
    CONSTRAINT different_thoughts CHECK (thought_a_id != thought_b_id),
    CONSTRAINT ordered_pair CHECK (thought_a_id < thought_b_id),
    UNIQUE(thought_a_id, thought_b_id)
);
CREATE INDEX idx_thought_pairs_unused ON thought_pairs(is_used_in_essay)
WHERE is_used_in_essay = FALSE;

-- 4. Essay 결과물
CREATE TABLE essays (
    id SERIAL PRIMARY KEY,
    type TEXT DEFAULT 'essay',
    title TEXT NOT NULL,
    outline JSONB NOT NULL,  -- ["1단: ...", "2단: ...", "3단: ..."]
    used_thoughts_json JSONB NOT NULL,
    reason TEXT NOT NULL,
    pair_id INTEGER NOT NULL REFERENCES thought_pairs(id),
    generated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_essays_generated_at ON essays(generated_at DESC);

-- 5. 처리 상태 추적
CREATE TABLE processing_status (
    id SERIAL PRIMARY KEY,
    raw_note_id UUID NOT NULL REFERENCES raw_notes(id) ON DELETE CASCADE,
    step TEXT NOT NULL,  -- 'extract_thoughts', 'create_embedding', 'select_pairs', 'generate_essay'
    status TEXT NOT NULL,  -- 'pending', 'processing', 'completed', 'failed'
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(raw_note_id, step)
);
CREATE INDEX idx_processing_status_step_status ON processing_status(step, status);

-- pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;
```

## API Endpoints

```
# Pipeline
POST /pipeline/import-from-notion    # Step 1: RAW 수용 (Database or Parent Page mode)
POST /pipeline/extract-thoughts      # Step 2: NORMALIZED 생성
POST /pipeline/select-pairs          # Step 3: ZK 페어 선택
POST /pipeline/generate-essays       # Step 4: Essay 생성
POST /pipeline/run-all               # 전체 파이프라인

# Hybrid C Strategy (NEW)
POST /pipeline/collect-candidates    # 전체 후보 수집 및 pair_candidates 저장
POST /pipeline/sample-initial        # 초기 100개 샘플 평가
POST /pipeline/score-candidates      # 배치 평가 (백그라운드)
GET  /essays/recommended             # AI 추천 Essay 후보 조회

# Essays
GET  /essays                         # Essay 목록 조회
GET  /essays/{id}                    # Essay 상세 조회

# Health
GET  /health                         # 서버 상태
```

## Import Modes

The `/pipeline/import-from-notion` endpoint supports two modes:

### Database Mode
- **Config:** Set `NOTION_DATABASE_ID` in .env
- **Behavior:** Fetches pages from database using `query_database()`
- **Content:** Reads from properties["본문"] field

### Parent Page Mode (NEW)
- **Config:** Set `NOTION_PARENT_PAGE_ID` in .env
- **Behavior:**
  1. Fetches child pages using `fetch_child_pages_from_parent()`
  2. For each child page, calls `fetch_page_blocks()` to get content
  3. Stores content in properties["본문"] (same as Database mode)
- **Performance:** ~3-5 minutes for 724 pages (rate limited)
- **Error Handling:** Failures on individual pages log warnings but don't stop import

**Note:** Either `NOTION_DATABASE_ID` or `NOTION_PARENT_PAGE_ID` must be set (validated at startup)

## Incremental Import (RPC-based Change Detection)

The import process uses **PostgreSQL RPC** for efficient change detection:

### Performance
- **Change detection time:** ~0.2s (constant, scales to 100k pages)
- **Accuracy:** 100% (unchanged pages correctly detected)
- **Improvement:** 270x faster than full table scan (60s → 0.2s)

### Behavior
1. **First run:** Imports all pages from Notion
2. **Subsequent runs:** Only imports new/updated pages
   - Unchanged pages: **skipped** (no DB write, no content fetch)
   - Updated pages: Re-imported with new content
   - New pages: Imported as usual

### Implementation
- **RPC Function:** `get_changed_pages(pages_data jsonb)`
- **Location:** Supabase PostgreSQL (public schema)
- **Input:** Array of `{id, last_edited_time}` from Notion API
- **Output:** `{new_page_ids[], updated_page_ids[], unchanged_count}`
- **Fallback:** Falls back to full table scan if RPC fails

### Success Rate Calculation
```python
# Skipped pages count as success (duplicate prevention is intentional)
success_count = imported_pages + skipped_pages
success_rate = (success_count / total_pages * 100)

# Job status
if success_rate >= 90:
    status = "completed"
else:
    status = "failed"
```

### SQL Schema
```sql
-- RPC function (deployed in Supabase)
CREATE OR REPLACE FUNCTION get_changed_pages(pages_data jsonb)
RETURNS jsonb AS $$
  -- See: backend/docs/supabase_import_jobs.sql
$$ LANGUAGE plpgsql;
```

## LLM Tasks

### 1. extract_thoughts (Step 2)
- **Input:** raw_note (title, content)
- **Output:** 1-5개의 ThoughtUnit (claim, context)
- **Model:** Claude 3.5 Sonnet

### 2. score_pairs (Step 3)
- **Input:** 후보 페어 목록 (similarity 0.05~0.35, 낮은 유사도 = 서로 다른 아이디어)
- **Output:** 각 페어의 논리적 확장 가능성 점수 (0-100)
- **Model:** Claude 3.5 Sonnet

### 3. generate_essay (Step 4)
- **Input:** 선택된 페어 + 출처 정보
- **Output:** Essay (title, outline[3], used_thoughts, reason)
- **Model:** Claude 3.5 Sonnet

## TypeScript Types (Frontend)

```typescript
interface NotionCredentials {
  apiKey: string;
  databaseId: string;
}

interface ThoughtUnit {
  id: number;
  claim: string;
  context: string | null;
  raw_note_id: string;
}

interface UsedThought {
  thought_id: number;
  claim: string;
  source_title: string;
  source_url: string;
}

interface Essay {
  id: number;
  type: string;
  title: string;
  outline: string[];  // 정확히 3개
  used_thoughts: UsedThought[];
  reason: string;
  generated_at: string;
}

interface PipelineResult {
  success: boolean;
  step1_imported: number;
  step2_thoughts: number;
  step3_pairs: number;
  step4_essays: number;
  errors: string[];
}
```

## Pydantic Schemas (Backend)

```python
# schemas/normalized.py
class ThoughtUnit(BaseModel):
    claim: str = Field(..., min_length=10, max_length=500)
    context: str | None = Field(None, max_length=200)

class ThoughtExtractionResult(BaseModel):
    thoughts: list[ThoughtUnit] = Field(..., min_length=1, max_length=5)

# schemas/essay.py
class UsedThought(BaseModel):
    thought_id: int
    claim: str
    source_title: str
    source_url: str = Field(..., pattern=r'^https?://')

class Essay(BaseModel):
    type: str = Field(default="essay")
    title: str = Field(..., min_length=5, max_length=100)
    outline: list[str] = Field(..., min_length=3, max_length=3)
    used_thoughts: list[UsedThought] = Field(..., min_length=1)
    reason: str = Field(..., max_length=300)
```

## Configuration

```bash
# Environment Variables
NOTION_API_KEY=secret_xxx
NOTION_DATABASE_ID=xxx
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=xxx
OPENAI_API_KEY=sk-xxx
ANTHROPIC_API_KEY=sk-ant-xxx

# Rate Limits
RATE_LIMIT_ANTHROPIC=5    # req/sec
RATE_LIMIT_OPENAI=10      # req/sec
RATE_LIMIT_NOTION=3       # req/sec

# Batch Processing
BATCH_SIZE=10
MAX_RETRIES=3

# pgvector
SIMILARITY_MIN=0.05  # 낮은 유사도 = 서로 다른 아이디어, 창의적 조합
SIMILARITY_MAX=0.35  # 너무 유사하면 새로운 통찰이 없음
EMBEDDING_DIMENSION=1536
```

## Code Conventions

- Python: Type hints required, async/await for I/O
- TypeScript: Strict mode, no any
- Error handling: Never bare except, always log with context
- Batch processing: Always gc.collect() after batch
- API keys: Never log, mask in error messages

## Agent Usage Policy

**MANDATORY: Always use specialized agents when available**

The following agents MUST be used proactively for their respective tasks:

### 1. test-automator (REQUIRED for all implementations)
- **When to use:** BEFORE completing ANY feature or code change
- **Why:** No feature is complete without tests
- **Example:** After implementing pagination logic, IMMEDIATELY use test-automator to create and run tests
- **Command:** Use Task tool with `subagent_type="test-automator"`

### 2. code-reviewer (REQUIRED before completion)
- **When to use:** After implementation, BEFORE merging or marking complete
- **Why:** Ensures code quality, security, and performance
- **Example:** After Phase 1 implementation passes tests, use code-reviewer to audit the changes

### 3. debugger (USE when errors occur)
- **When to use:** Encountering errors, unexpected behavior, or test failures
- **Why:** Systematic debugging approach with proper tooling
- **Example:** API 429 errors, import failures, pagination bugs

### 4. Explore (USE for codebase analysis)
- **When to use:** Need to quickly find files by patterns or search code
- **Why:** Faster than manual Glob/Grep for complex searches
- **Example:** "Find all files that handle Notion API pagination"

### 5. prompt-engineer (REQUIRED for LLM work)
- **When to use:** Working with Claude/OpenAI API calls, JSON output, prompts
- **Why:** Specialized in LLM prompt design and output formatting
- **Example:** Designing prompts for thought extraction or essay generation

### 6. supabase-specialist (REQUIRED for database work)
- **When to use:** Supabase operations, pgvector similarity search, PostgreSQL queries, schema changes
- **Why:** Expert in Supabase patterns, RLS policies, and vector operations
- **Example:** Implementing batch upsert, pgvector similarity queries, stored procedures

### 7. nextjs-developer (REQUIRED for frontend work)
- **When to use:** Next.js 14 frontend, React components, API integration, .tsx files
- **Why:** Specialized in Next.js App Router, Server Components, and client patterns
- **Example:** Creating essay viewer components, implementing pipeline controls

### 8. fastapi-architect (REQUIRED for backend work)
- **When to use:** FastAPI backend, async patterns, pipeline architecture, service layer design
- **Why:** Expert in FastAPI patterns, dependency injection, and async operations
- **Example:** Designing router structure, implementing async services, pipeline endpoints

### 9. Plan (USE for implementation planning)
- **When to use:** Before starting complex implementations, need architectural decisions
- **Why:** Creates detailed step-by-step plans with file locations and code examples
- **Example:** Planning Phase 2 (block content collection) or Phase 3 (rate limiting)

### 10. file-organizer (USE for cleanup)
- **When to use:** Need to clean up temporary files or organize project structure
- **Why:** Safe file operations with user confirmation
- **Example:** Removing unused test files, organizing old plan files

### General Rules
- **Proactive Usage:** Don't wait for user to ask - use agents automatically when applicable
- **Parallel Execution:** When possible, run multiple agents in parallel (single message, multiple Task calls)
- **Documentation:** Always document agent usage in commit messages and plan files
- **Test First:** ALWAYS use test-automator after any code change before marking complete
- **Review Last:** ALWAYS use code-reviewer before final completion of major features

## Plan File Management

**IMPORTANT: Plan File Synchronization Rule**

All plan files must be synchronized between two locations:
1. **User home directory:** `~/.claude/plans/` (Claude Code default location)
2. **Project directory:** `<project_root>/.claude/plans/` (for version control and IDE visibility)

**When to synchronize:**
- When a new plan file is created
- When an existing plan file is updated
- When exiting plan mode

**How to synchronize:**
```bash
# After creating or updating plan files in ~/.claude/plans/
cp ~/.claude/plans/*.md <project_root>/.claude/plans/
```

**Why this matters:**
- Project .claude/plans/ is visible in Cursor IDE
- User ~/.claude/plans/ is where Claude Code stores plans by default
- Keeping both in sync ensures plans are accessible everywhere and can be version controlled
