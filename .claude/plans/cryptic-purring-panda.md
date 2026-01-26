# 노션 기반 아이디어 조합 서비스 MVP 최종 구현 계획

## 프로젝트 개요

기존 backend 코드를 최대한 재사용하면서 **RAW → NORMALIZED → ZK** 아키텍처로 리팩터링하여 "노션 위 사고 증폭 엔진" MVP를 구축합니다.

### Definition of Done
1. ✅ Notion DB에서 메모 읽기 (RAW 레이어)
2. ✅ 각 메모를 사고 단위로 추출 (NORMALIZED 레이어)
3. ✅ 논리적 확장 가능한 연결 1~2쌍 선택 (ZK 레이어)
4. ✅ 글감 결과물 카드 1~3개 생성
5. ✅ **결과물 필수 항목**: 제목 / 3단 아웃라인 / 사용된 사고 단위 / 연결 이유(3줄) / 근거 노션 페이지 링크
6. ✅ **최소 UI**: 로그인, DB 선택, Run 버튼, 결과 카드 목록

### 기술 스택 (사용자 결정)
- **Frontend**: React/Next.js 별도 프로젝트
- **Backend**: FastAPI (기존 유지)
- **Notion 인증**: API Key 방식 유지
- **데이터 저장**: Supabase DB
- **테스트 규모**: 50-100개 메모
- **암호화**: RAW 레이어만 (MVP+1부터)

---

## 데이터 아키텍처

### Supabase 테이블 구조 (5개 테이블)

```sql
-- 1. RAW 레이어: Notion 원본 (MVP+1부터 암호화)
CREATE TABLE raw_notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    notion_page_id TEXT UNIQUE NOT NULL,
    notion_url TEXT NOT NULL,
    title TEXT,  -- MVP+1부터 암호화
    content TEXT,  -- MVP+1부터 암호화
    properties_json JSONB DEFAULT '{}'::jsonb,
    notion_created_time TIMESTAMPTZ NOT NULL,
    notion_last_edited_time TIMESTAMPTZ NOT NULL,
    imported_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_raw_notes_notion_page_id ON raw_notes(notion_page_id);

-- 2. NORMALIZED 레이어: 사고 단위 (평문 유지 - pgvector 검색 위해)
CREATE TABLE thought_units (
    id SERIAL PRIMARY KEY,
    raw_note_id UUID NOT NULL REFERENCES raw_notes(id) ON DELETE CASCADE,
    claim TEXT NOT NULL,  -- 평문
    context TEXT,  -- 평문
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
    outline JSONB NOT NULL,  -- [str, str, str]
    used_thoughts_json JSONB NOT NULL,
    reason TEXT NOT NULL,
    pair_id INTEGER NOT NULL REFERENCES thought_pairs(id),
    generated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_essays_generated_at ON essays(generated_at DESC);

-- 5. 처리 상태 추적 (LLM 실패 대응 및 재시도)
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
CREATE INDEX idx_processing_status_retry ON processing_status(status, retry_count)
WHERE status = 'failed' AND retry_count < 3;

-- 필수: pgvector extension 활성화
CREATE EXTENSION IF NOT EXISTS vector;
```

### 데이터 흐름
```
Notion DB
  ↓ (Step 1: import)
RAW (원본 보존, MVP+1부터 암호화)
  ↓ (Step 2: extract + embed, 복호화하여 처리)
NORMALIZED (사고 단위 + 임베딩, 평문 저장)
  ↓ (Step 3: calculate similarity + score pairs)
ZK (약한 연결 페어 선택, 평문)
  ↓ (Step 4: generate essay)
Essay (글감 생성, 평문)
```

**암호화 정책**:
- RAW만 암호화 → NORMALIZED 추출 시 복호화 → NORMALIZED 이후는 평문
- 이유: pgvector 임베딩 검색 필수, 성능 영향 최소 (~5%)

---

## Backend 구현 (단계별)

### Step 0: 환경 준비 ✓

**재사용 가능**:
- `NotionService` (services/notion_service.py)
- `AIService` (services/ai_service.py)
- FastAPI 앱 구조 (main.py, config.py)
- Pydantic 스키마 패턴

**신규 구현**:
- Supabase 연동
- RAW/NORMALIZED/ZK 데이터 모델

---

### Step 1: RAW 수용 파이프라인

**생성 파일**:
- `backend/services/supabase_service.py` - Supabase CRUD + 연결 풀링
- `backend/schemas/raw.py` - RawNote, RawNoteCreate
- `backend/routers/pipeline.py` - 파이프라인 엔드포인트

**핵심 로직**:
```python
# services/supabase_service.py
class SupabaseService:
    def __init__(self):
        # HTTP 연결 풀링
        self.http_client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            timeout=httpx.Timeout(30.0)
        )
        self.client = create_client(settings.supabase_url, settings.supabase_key,
                                     options={'http_client': self.http_client})

    async def upsert_raw_note(self, note: RawNoteCreate) -> dict:
        """notion_page_id 기준 upsert"""

    async def get_raw_note_ids(self) -> List[str]:
        """메모리 절약: ID 목록만 먼저 조회"""

# routers/pipeline.py
@router.post("/pipeline/import-from-notion")
async def import_from_notion(page_size: int = Query(default=100, le=100)):
    # NotionService.query_database() 재사용
    # 페이지네이션 처리 (100개 초과 시)
    # RAW 테이블 저장 (중복 방지)
```

**재사용**: `NotionService.query_database()`, `_extract_property_value()`

**테스트**:
```bash
curl -X POST "http://localhost:8000/pipeline/import-from-notion?page_size=10"
# 예상 출력: {"success": true, "imported_count": 10, "skipped_count": 0}
```

---

### Step 2: NORMALIZED 생성 (사고 단위 추출)

**생성 파일**:
- `backend/schemas/normalized.py` - ThoughtUnit, ThoughtExtractionResult
- `backend/schemas/processing.py` - ProcessingStatus
- `backend/services/ai_service.py` - `extract_thoughts()` 추가

**핵심 로직**:
```python
# services/ai_service.py
@handle_llm_errors  # 에러 핸들링 데코레이터
async def extract_thoughts(self, raw_note: RawNote) -> ThoughtExtractionResult:
    """
    LLM 프롬프트:
    '다음 메모에서 독립적인 사고 단위를 추출하세요.
    각 사고 단위는 {claim: str, context: str} 형식.
    최소 1개, 최대 5개. JSON 배열로 출력.'

    - 모델: Claude 3.5 Sonnet
    - 토큰 제한: 본문 8000자 제한 (자동 truncate)
    - 안전장치: JSON 파싱 실패 시 빈 배열
    """

# routers/pipeline.py
@router.post("/pipeline/extract-thoughts")
async def extract_thoughts_endpoint(batch_size: int = Query(default=10, le=100)):
    """
    메모리 효율적 처리:
    1. RAW note ID만 먼저 조회 (메모리 절약)
    2. batch_size개씩 full content 조회
    3. 병렬 LLM 호출 (asyncio.gather)
    4. thought_units 즉시 저장
    5. 임베딩 생성 후 즉시 업데이트
    6. 메모리 해제 (gc.collect())
    7. 다음 배치로 진행

    메모리 사용량:
    - batch_size=10: ~7MB
    - batch_size=100: ~67MB (권장 최대)
    """

    rate_limiter = RateLimiter()
    raw_note_ids = await supabase_service.get_raw_note_ids()

    for i in range(0, len(raw_note_ids), batch_size):
        batch_ids = raw_note_ids[i:i+batch_size]
        batch_notes = await supabase_service.get_raw_notes_by_ids(batch_ids)

        # 병렬 처리
        await rate_limiter.wait('anthropic')
        tasks = [ai_service.extract_thoughts(note) for note in batch_notes]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 즉시 DB 저장 + 임베딩 생성
        for note, result in zip(batch_notes, results):
            if not isinstance(result, Exception):
                await save_thoughts_to_db(note.id, result.thoughts)

        # 메모리 해제
        del batch_notes, results
        gc.collect()
```

**처리 상태 추적** (LLM 실패 대응):
```python
# 각 메모별로 처리 상태 기록
async def extract_thoughts_endpoint():
    for raw_note in raw_notes:
        try:
            await mark_processing_status(raw_note.id, 'extract_thoughts', 'processing')
            result = await ai_service.extract_thoughts(raw_note)
            await save_thoughts_to_db(raw_note.id, result.thoughts)
            await mark_processing_status(raw_note.id, 'extract_thoughts', 'completed')
        except Exception as e:
            await mark_processing_status(raw_note.id, 'extract_thoughts', 'failed', str(e))
            # 한 메모 실패가 전체 중단 안 함
```

**재사용**: `AIService.create_embedding()`, `generate_content_with_claude()`

**테스트**:
```bash
curl -X POST "http://localhost:8000/pipeline/extract-thoughts?batch_size=10"
# Supabase thought_units 테이블 확인 (embedding NOT NULL)
```

---

### Step 3: Pair 선택 (약한 연결)

**생성 파일**:
- `backend/schemas/zk.py` - ThoughtPair, PairScoringResult

**핵심 로직**:
```python
# services/supabase_service.py
async def calculate_similarity_pairs(min_similarity=0.3, max_similarity=0.7, top_n=50):
    """
    pgvector <=> 연산자로 코사인 유사도 계산

    SQL:
    SELECT a.id, b.id, 1 - (a.embedding <=> b.embedding) as similarity
    FROM thought_units a, thought_units b
    WHERE a.id < b.id
    AND 1 - (a.embedding <=> b.embedding) BETWEEN 0.3 AND 0.7
    ORDER BY similarity DESC
    LIMIT 50
    """

# services/ai_service.py
async def score_pairs(self, pairs: List[dict]) -> PairScoringResult:
    """
    LLM 프롬프트:
    '다음 사고 단위 쌍들을 평가하세요.
    논리적으로 확장 가능한가? (반대 의견, 보완 관계, 인과 관계)
    JSON: [{pair_index, score, reason}]'

    - 배치 처리: 20개씩 평가 (토큰 효율)
    """

# routers/pipeline.py
@router.post("/pipeline/select-pairs")
async def select_pairs_endpoint(top_n: int = Query(default=10, le=20)):
    # 1. 유사도 기반 후보 50개 생성
    candidates = await supabase_service.calculate_similarity_pairs()

    # 2. LLM으로 논리적 확장 가능성 평가
    scored = await ai_service.score_pairs(candidates)

    # 3. 상위 N개 선택
    top_pairs = sorted(scored.pairs, key=lambda x: x['score'], reverse=True)[:top_n]

    # 4. thought_pairs 테이블 저장
    await supabase_service.save_thought_pairs(top_pairs)
```

**테스트**:
```bash
curl -X POST "http://localhost:8000/pipeline/select-pairs?top_n=5"
# similarity_score 0.3~0.7 확인
```

---

### Step 4: Essay 결과물 생성

**생성 파일**:
- `backend/schemas/essay.py` - Essay, ThoughtReference (엄격한 스키마)
- `backend/routers/essays.py` - Essay 조회 전용 엔드포인트

**핵심 로직**:
```python
# services/ai_service.py
async def generate_essay(thought_a, thought_b, pair_reason, source_a, source_b) -> Essay:
    """
    LLM 프롬프트:
    '두 사고 단위를 바탕으로 글감을 제안하세요.
    출력 JSON:
    {
      "type": "essay",
      "title": "글감 제목",
      "outline": ["1단: ...", "2단: ...", "3단: ..."],
      "used_thoughts": [
        {"thought_id": ..., "claim": ..., "source_title": ..., "source_url": ...}
      ],
      "reason": "3줄 이내"
    }'

    - Pydantic 검증으로 스키마 강제
    - outline 정확히 3개
    - Notion URL 포함
    """

# routers/pipeline.py
@router.post("/pipeline/generate-essays")
async def generate_essays_endpoint(max_essays: int = Query(default=3, le=10)):
    # 1. 미사용 페어 조회 (is_used_in_essay=false)
    pairs = await supabase_service.get_unused_pairs(limit=max_essays)

    # 2. 각 페어에 대해 generate_essay() 호출
    essays = []
    for pair in pairs:
        essay = await ai_service.generate_essay(pair)
        await supabase_service.save_essay(essay)
        await supabase_service.mark_pair_used(pair.id)
        essays.append(essay)

    return {"success": True, "essays": essays}
```

**출력 스키마 (엄격)**:
```json
{
  "type": "essay",
  "title": "인공지능과 창의성: 경계에서 만나다",
  "outline": [
    "1단: AI의 한계와 인간 고유의 창의성",
    "2단: 두 영역의 협력 가능성",
    "3단: 미래 창작 환경의 변화"
  ],
  "used_thoughts": [
    {
      "thought_id": 12,
      "claim": "AI는 패턴을 학습하지만 진정한 창의성은 없다",
      "source_title": "AI와 예술",
      "source_url": "https://notion.so/..."
    }
  ],
  "reason": "AI의 한계와 창의성의 본질을 대비시키면서, 새로운 창작 패러다임을 탐구할 수 있는 흥미로운 조합",
  "generated_at": "2026-01-05T10:30:00Z"
}
```

**테스트**:
```bash
curl -X POST "http://localhost:8000/pipeline/generate-essays?max_essays=2"
# outline 3개, used_thoughts에 Notion URL 포함 확인
```

---

### Step 5: 통합 파이프라인 + 안전장치

**통합 엔드포인트**:
```python
@router.post("/pipeline/run-all")
async def run_full_pipeline(
    page_size: int = Query(default=100, le=100),
    top_pairs: int = Query(default=10, le=20),
    max_essays: int = Query(default=3, le=10)
):
    """
    전체 파이프라인 한 번에 실행
    Step 1 → 2 → 3 → 4
    각 단계별 성공/실패 카운트 반환
    """
    results = {
        "step1_imported": 0,
        "step2_thoughts": 0,
        "step3_pairs": 0,
        "step4_essays": 0,
        "errors": []
    }

    try:
        # Step 1-4 순차 실행
        ...
    except Exception as e:
        results["errors"].append(str(e))

    return results
```

**안전장치**:

1. **LLM 재시도 로직**:
```python
async def extract_thoughts_with_retry(raw_note, max_retries=2):
    for attempt in range(max_retries):
        result = await extract_thoughts(raw_note)
        if result.total_extracted > 0:
            return result
    # 최종 실패: 메모 전체를 단일 사고 단위로 저장
    return fallback_thought_unit(raw_note)
```

2. **JSON 파싱 강화**:
```python
def safe_json_parse(content: str):
    # Markdown code block 제거
    content = re.sub(r'```json\s*', '', content)
    # 주석 제거
    content = re.sub(r'//.*', '', content)
    # 파싱
    return json.loads(content)
```

3. **데이터 정합성 검증**:
```python
@router.get("/pipeline/validate")
async def validate_data_integrity():
    """
    - RAW notes without thoughts
    - Thoughts without embeddings
    - Pairs without essays
    """
```

**테스트**:
```bash
curl -X POST "http://localhost:8000/pipeline/run-all?page_size=50&max_essays=3"
# 예상 소요 시간: ~3분 (50개 메모)
```

---

## Frontend 구현 (Next.js)

### 프로젝트 구조
```
frontend/
├── src/
│   ├── app/
│   │   ├── page.tsx              # 메인 (Run + 결과)
│   │   ├── login/page.tsx        # API Key 입력
│   │   └── layout.tsx
│   ├── components/
│   │   ├── EssayCard.tsx         # 결과물 카드
│   │   ├── DatabaseSelector.tsx  # DB 선택
│   │   └── PipelineControl.tsx   # Run 버튼 + 진행 상태
│   ├── lib/
│   │   ├── api.ts                # Backend API 클라이언트
│   │   └── types.ts
│   └── hooks/
│       └── usePipeline.ts        # 파이프라인 상태 관리
```

### 핵심 컴포넌트

**1. 로그인 페이지** (`/login`):
```tsx
// 입력: Notion API Key, Database ID
// 저장: localStorage (MVP)
// 검증: /notion/database/info 호출
```

**2. PipelineControl**:
```tsx
// Run 버튼 클릭 시:
- /pipeline/run-all 호출
- 진행 상태 표시:
  "Step 1: Importing... (50 / 100)"
  "Step 2: Extracting thoughts... (150 thoughts)"
  "Step 3: Selecting pairs... (10 pairs)"
  "Step 4: Generating essays... (3 / 3)"
- 완료: "✓ Complete! Generated 3 essays"
```

**3. EssayCard**:
```tsx
// 카드 표시:
- 제목 (큰 폰트)
- 아웃라인 (3단, 접기 가능)
- 사용된 사고 단위 (Notion 링크 포함)
- 연결 이유 (3줄)
- 생성 시각
```

### API 연동
```typescript
// lib/api.ts
export class BackendClient {
  async runPipeline(options: PipelineOptions): Promise<PipelineResult> {
    const response = await fetch(
      `${API_BASE}/pipeline/run-all?page_size=${options.pageSize}&max_essays=${options.maxEssays}`,
      {
        method: 'POST',
        headers: {
          'X-Notion-API-Key': this.apiKey,
          'X-Notion-Database-ID': this.databaseId,
        },
      }
    );
    return response.json();
  }
}
```

**기술 스택**:
- Next.js 14 (App Router)
- Tailwind CSS
- React Hooks (상태 관리)

---

## 성능 최적화

### 병렬 처리 + Rate Limiting
```python
class RateLimiter:
    """API Rate Limit 관리"""
    def __init__(self):
        self.min_interval = {
            'openai': 0.1,    # 초당 10 requests
            'anthropic': 0.2  # 초당 5 requests
        }

    async def wait(self, service: str):
        # 최소 간격 대기

# 메모리 효율적 배치 처리
async def process_in_batches(raw_notes, batch_size=10):
    for i in range(0, len(raw_notes), batch_size):
        batch = raw_notes[i:i+batch_size]

        # 병렬 LLM 호출
        await rate_limiter.wait('anthropic')
        results = await asyncio.gather(*[extract_thoughts(note) for note in batch])

        # 즉시 DB 저장
        for note, result in zip(batch, results):
            await save_thoughts_to_db(note.id, result.thoughts)

        # 메모리 해제
        del batch, results
        gc.collect()
```

**메모리 사용량** (100개 메모):
- 원문: ~500KB
- LLM 응답: ~150KB
- 임베딩: ~18KB
- **총: ~67MB (안전)**

**성능 개선**:
- 순차 처리: 200초
- 병렬 처리: 20초 (10배 속도)

---

## 에러 처리

### 커스텀 Exception
```python
class LLMError(Exception): pass
class RateLimitError(LLMError): pass
class TimeoutError(LLMError): pass
class ValidationError(LLMError): pass

@handle_llm_errors  # 데코레이터
async def extract_thoughts(raw_note):
    # LLM 호출
```

### 지수 백오프 재시도
```python
async def retry_with_backoff(func, max_retries=3):
    """1초 → 2초 → 4초 대기 후 재시도"""
    delay = 1.0
    for attempt in range(max_retries):
        try:
            return await func()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(delay)
            delay *= 2
```

---

## 파일 변경 요약

### 생성할 파일 (12개)

**Backend**:
1. `services/supabase_service.py` - Supabase CRUD + 연결 풀링
2. `services/rate_limiter.py` - API Rate Limit 관리
3. `schemas/raw.py` - RawNote 모델
4. `schemas/normalized.py` - ThoughtUnit 모델
5. `schemas/zk.py` - ThoughtPair 모델
6. `schemas/essay.py` - Essay 모델 (엄격)
7. `schemas/processing.py` - ProcessingStatus 모델
8. `routers/pipeline.py` - 파이프라인 엔드포인트
9. `routers/essays.py` - Essay 조회
10. `utils/validators.py` - JSON 파싱, 검증
11. `utils/error_handlers.py` - 통합 에러 핸들링
12. `middleware/auth.py` - API Key 검증 (선택)

**Frontend**: Next.js 프로젝트 전체 (8+ 파일)

### 수정할 파일 (3개)
1. `services/ai_service.py` - 3개 함수 추가
   - `extract_thoughts()`
   - `score_pairs()`
   - `generate_essay()`
2. `services/notion_service.py` - 1개 함수 추가 (선택)
   - `get_page_content()` - block 조회
   - `query_all_pages()` - 페이지네이션
3. `main.py` - pipeline, essays 라우터 등록

### 재사용 (수정 없음)
- `config.py` - 환경변수 관리
- `NotionService.query_database()` - 메모 조회
- `AIService.create_embedding()` - 임베딩 생성
- `AIService.generate_content_with_claude()` - LLM 호출

---

## 환경 변수

```bash
# backend/.env
# 기존 변수
NOTION_API_KEY=...
NOTION_DATABASE_ID=...
SUPABASE_URL=...
SUPABASE_KEY=...
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...

# 추가 (선택)
LOG_LEVEL=INFO
MAX_RETRIES=3
BATCH_SIZE=10
SIMILARITY_MIN=0.3
SIMILARITY_MAX=0.7
RATE_LIMIT_OPENAI=10
RATE_LIMIT_ANTHROPIC=5

# MVP+1 이후 (암호화)
# ENCRYPTION_KEY=<32-byte base64>  # RAW 암호화용
```

---

## 구현 우선순위

### Must Have (MVP):
1. ✅ Step 1~4 파이프라인 전체
2. ✅ 엄격한 Essay JSON 스키마
3. ✅ Frontend 최소 UI
4. ✅ Supabase 저장/조회
5. ✅ 기본 에러 핸들링
6. ✅ 처리 상태 추적 (processing_status)

### Should Have (MVP+):
1. 병렬 처리 (성능 개선)
2. LLM 재시도 로직
3. 로깅 시스템
4. 데이터 정합성 검증

### MVP+1 (보안 강화):
1. **RAW 암호화** (AES-256, Fernet)
   - `raw_notes.title`, `raw_notes.content` 암호화
   - NORMALIZED 추출 시 복호화
   - 성능 영향: ~5%
2. Supabase RLS 활성화
3. 키 관리 (환경변수 → AWS KMS)

### Nice to Have (v2):
1. WebSocket 실시간 진행 상태
2. Essay 편집 기능
3. 메모 필터링 (날짜/태그)
4. 사용자별 DB 관리

---

## 암호화 전략 (MVP+1)

### RAW만 암호화 (권장)

**이유**:
1. RAW가 가장 민감 (원본 메모)
2. NORMALIZED는 평문 유지 → pgvector 검색 가능
3. 성능 영향 최소 (~5%)
4. 구현 복잡도 낮음

**구현** (MVP+1):
```python
# services/encryption_service.py
from cryptography.fernet import Fernet

class EncryptionService:
    def __init__(self):
        self.key = os.getenv('ENCRYPTION_KEY').encode()
        self.cipher = Fernet(self.key)

    def encrypt(self, plaintext: str) -> str:
        return self.cipher.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self.cipher.decrypt(ciphertext.encode()).decode()

# RAW 저장 시 자동 암호화
async def upsert_raw_note(note: RawNoteCreate):
    encryption_service = EncryptionService()
    encrypted_note = {
        **note.dict(),
        'title': encryption_service.encrypt(note.title),
        'content': encryption_service.encrypt(note.content)
    }
    return await self.client.table('raw_notes').upsert(encrypted_note).execute()

# NORMALIZED 추출 시 자동 복호화
async def extract_thoughts_endpoint():
    encryption_service = EncryptionService()

    encrypted_raw = await supabase_service.get_raw_note(raw_note_id)

    # 메모리에서만 복호화 (DB는 암호화 상태 유지)
    decrypted_raw = {
        **encrypted_raw,
        'title': encryption_service.decrypt(encrypted_raw['title']),
        'content': encryption_service.decrypt(encrypted_raw['content'])
    }

    # LLM 호출 (복호화된 데이터)
    result = await ai_service.extract_thoughts(decrypted_raw)

    # NORMALIZED는 평문으로 저장 (임베딩 검색 위해)
    await save_thoughts_to_db(result.thoughts)
```

**성능 비교** (50개 메모):

| 전략 | 암호화 범위 | 복호화 횟수 | 처리 시간 | pgvector | 보안 |
|------|------------|------------|----------|----------|------|
| 없음 (MVP) | - | 0 | 3분 | ✅ | ⭐ |
| RAW만 (MVP+1) | RAW | 50회 | 3분 10초 (+5%) | ✅ | ⭐⭐⭐⭐ |
| NORMALIZED까지 | RAW+NORM | 200회 | 4분 30초 (+50%) | ❌ | ⭐⭐⭐⭐⭐ |

**NORMALIZED 암호화는 비권장**:
- pgvector 임베딩 검색 불가능 (치명적)
- 성능 저하 심각 (~50%)
- 실용성 낮음

---

## 예상 타임라인

- **Week 1**: Backend 인프라 (Supabase, 스키마, Pydantic 모델)
- **Week 2**: 파이프라인 Step 1~2 (RAW 수용, NORMALIZED 추출)
- **Week 3**: 파이프라인 Step 3~4 (Pair 선택, Essay 생성)
- **Week 4**: Frontend (Next.js, 컴포넌트, API 연동)
- **Week 5**: 통합 테스트, 성능 최적화, 버그 수정

**목표**: 5주 내 MVP 완성, 50-100개 메모로 검증

---

## 최종 체크리스트

### Supabase 설정:
- [ ] pgvector extension 활성화
- [ ] 5개 테이블 생성 SQL 실행
- [ ] 인덱스 생성 (pgvector는 1000개 이상일 때)

### Backend:
- [ ] Step 1: RAW 수용 (10개 메모 테스트)
- [ ] Step 2: 사고 단위 추출 (35개 생성 확인)
- [ ] Step 3: 페어 선택 (5쌍 생성)
- [ ] Step 4: Essay 생성 (2개 결과물)
- [ ] Step 5: 전체 파이프라인 (50개 메모, ~3분)
- [ ] 에러 핸들링 테스트 (LLM 실패, JSON 파싱 실패)

### Frontend:
- [ ] 로그인 페이지 동작 확인
- [ ] Run 버튼 → API 호출 → 진행 상태 표시
- [ ] 결과 카드 3개 렌더링
- [ ] Notion 링크 클릭 → 페이지 이동

### MVP+1 (선택):
- [ ] RAW 암호화 구현 (EncryptionService)
- [ ] 복호화 로직 테스트
- [ ] 성능 영향 측정 (~5% 예상)

---

## 핵심 파일 경로

### 재사용:
- `backend/services/notion_service.py`
- `backend/services/ai_service.py`
- `backend/config.py`
- `backend/main.py`

### 신규 생성:
- `backend/services/supabase_service.py`
- `backend/routers/pipeline.py`
- `backend/schemas/` (raw, normalized, zk, essay, processing)
- `backend/utils/` (validators, error_handlers)
- `frontend/` (전체)

### 수정:
- `backend/services/ai_service.py` (3개 함수 추가)
- `backend/services/notion_service.py` (2개 함수 추가, 선택)
- `backend/main.py` (라우터 등록)

---

**최종 정리**: 이 계획은 기존 코드를 최대한 재사용하면서, RAW → NORMALIZED → ZK 아키텍처로 깔끔하게 리팩터링하는 실행 가능한 로드맵입니다. 각 단계가 독립적으로 테스트 가능하며, 메모리 관리, 에러 처리, 성능 최적화가 모두 고려되었습니다. MVP+1부터 RAW 암호화를 추가하여 보안을 강화할 수 있습니다.
