# 하이브리드 C 전략 구현 플랜

## 개요

**목표**: 노션 메모 기반 Essay 생성 시스템에서 "후보 보존"과 "효율성"을 동시에 달성하는 하이브리드 전략 구현

**핵심 아이디어**:
```
pair_candidates (전체 30,000개 후보 보관)
  ↓
초기 샘플링 (100개 평가)
  ↓
배치 워커 (점진적 평가)
  ↓
thought_pairs (고품질만 저장)
  ↓
UI 추천/Essay 생성 (빠름)
```

**예상 성과**:
- 데이터 손실: 0% (전체 후보 보관)
- 초기 평가: 100개 (기존 5개 → 20배 증가)
- 점진적 확장: 30,000개 완전 활용 가능
- UI 응답: <100ms 유지

---

## 현재 상태

### 기존 구조
- **DB**: thought_pairs 테이블 (선별된 페어만, claude_score 컬럼 없음)
- **Step 3**: Top-K → Claude 평가 → thought_pairs 저장
- **문제**: 99%의 후보가 평가 없이 버려짐 (30,000개 중 5-20개만)

### 기존 모듈
- `services/supabase_service.py`: find_candidate_pairs(), insert_thought_pairs_batch()
- `services/ai_service.py`: score_pairs() (Claude 평가)
- `routers/pipeline.py`: /select-pairs 엔드포인트

---

## Phase 1: DB 스키마 마이그레이션

### 1.1 pair_candidates 테이블 생성

**파일**: `backend/docs/supabase_migrations/006_create_pair_candidates.sql`

```sql
-- 전체 후보 페어 보관용 테이블
CREATE TABLE IF NOT EXISTS pair_candidates (
    id BIGSERIAL PRIMARY KEY,
    thought_a_id INTEGER NOT NULL REFERENCES thought_units(id) ON DELETE CASCADE,
    thought_b_id INTEGER NOT NULL REFERENCES thought_units(id) ON DELETE CASCADE,
    similarity FLOAT NOT NULL CHECK (similarity >= 0 AND similarity <= 1),
    raw_note_id_a UUID NOT NULL,
    raw_note_id_b UUID NOT NULL,

    -- LLM 평가 관련
    llm_score INTEGER CHECK (llm_score >= 0 AND llm_score <= 100),
    llm_status TEXT DEFAULT 'pending' CHECK (llm_status IN ('pending', 'processing', 'completed', 'failed')),
    llm_attempts INTEGER DEFAULT 0,
    last_evaluated_at TIMESTAMPTZ,
    evaluation_error TEXT,

    -- 메타데이터
    created_at TIMESTAMPTZ DEFAULT NOW(),

    -- 제약조건
    CONSTRAINT pc_different_thoughts CHECK (thought_a_id != thought_b_id),
    CONSTRAINT pc_ordered_pair CHECK (thought_a_id < thought_b_id),
    UNIQUE(thought_a_id, thought_b_id)
);

-- 인덱스
CREATE INDEX idx_pc_llm_status ON pair_candidates(llm_status) WHERE llm_status = 'pending';
CREATE INDEX idx_pc_llm_score ON pair_candidates(llm_score DESC) WHERE llm_score IS NOT NULL;
CREATE INDEX idx_pc_raw_notes ON pair_candidates(raw_note_id_a, raw_note_id_b);
CREATE INDEX idx_pc_similarity ON pair_candidates(similarity);

COMMENT ON TABLE pair_candidates IS '전체 후보 페어 보관 및 배치 평가 관리';
```

**배포 방법**:
1. Supabase Dashboard → SQL Editor
2. 위 SQL 복사 후 실행
3. 테이블 생성 확인: `SELECT COUNT(*) FROM pair_candidates;`

### 1.2 thought_pairs 테이블 확장

**파일**: `backend/docs/supabase_migrations/007_extend_thought_pairs.sql`

```sql
-- thought_pairs 테이블에 컬럼 추가
ALTER TABLE thought_pairs
ADD COLUMN IF NOT EXISTS claude_score INTEGER CHECK (claude_score >= 0 AND claude_score <= 100);

ALTER TABLE thought_pairs
ADD COLUMN IF NOT EXISTS quality_tier TEXT DEFAULT 'standard' CHECK (quality_tier IN ('standard', 'premium', 'excellent'));

ALTER TABLE thought_pairs
ADD COLUMN IF NOT EXISTS essay_content TEXT;

-- 인덱스 추가
CREATE INDEX IF NOT EXISTS idx_tp_claude_score ON thought_pairs(claude_score DESC) WHERE claude_score IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tp_quality_tier ON thought_pairs(quality_tier, is_used_in_essay) WHERE is_used_in_essay = FALSE;

COMMENT ON COLUMN thought_pairs.claude_score IS 'Claude LLM evaluation score (0-100)';
COMMENT ON COLUMN thought_pairs.quality_tier IS 'Quality tier: standard(65-84), premium(85-94), excellent(95-100)';
COMMENT ON COLUMN thought_pairs.essay_content IS 'Optional pre-generated essay content for UI preview';
```

**마이그레이션 전략**:
- 기존 데이터 영향 없음 (ADD COLUMN IF NOT EXISTS)
- NULL 허용 (점진적 채움)
- 롤백: `ALTER TABLE thought_pairs DROP COLUMN claude_score;`

---

## Phase 2: Python 서비스 레이어

### 2.1 Pydantic 스키마 확장

**파일**: `backend/schemas/zk.py`

**추가할 클래스**:

```python
from pydantic import BaseModel, Field
from datetime import datetime

class PairCandidateCreate(BaseModel):
    """pair_candidates 테이블 저장용"""
    thought_a_id: int
    thought_b_id: int
    similarity: float = Field(..., ge=0, le=1)
    raw_note_id_a: str  # UUID string
    raw_note_id_b: str

class PairCandidateDB(PairCandidateCreate):
    """DB 조회 모델"""
    id: int
    llm_score: int | None = None
    llm_status: str = "pending"
    llm_attempts: int = 0
    last_evaluated_at: datetime | None = None
    created_at: datetime

class PairCandidateBatch(BaseModel):
    """배치 저장 응답"""
    inserted_count: int
    duplicate_count: int
    error_count: int

class ThoughtPairCreateExtended(BaseModel):
    """확장된 thought_pairs 저장용"""
    thought_a_id: int
    thought_b_id: int
    similarity_score: float
    connection_reason: str
    claude_score: int | None = Field(None, ge=0, le=100)
    quality_tier: str | None = Field(None, pattern="^(standard|premium|excellent)$")
```

**통합 지점**: 기존 `ThoughtPairCreate`는 하위 호환성 유지

### 2.2 supabase_service.py 확장

**파일**: `backend/services/supabase_service.py`

**추가할 메서드**:

#### 1) insert_pair_candidates_batch()
```python
async def insert_pair_candidates_batch(
    self,
    candidates: List[PairCandidateCreate],
    batch_size: int = 1000
) -> PairCandidateBatch:
    """
    전체 후보를 pair_candidates에 대량 저장.

    Performance: 30,000개 저장 시간 ~3분 이내
    """
    # 배치 처리 (1000개씩)
    # ON CONFLICT DO NOTHING (중복 무시)
```

#### 2) get_pending_candidates()
```python
async def get_pending_candidates(
    self,
    limit: int = 100,
    similarity_range: tuple[float, float] = (0.05, 0.35)
) -> List[dict]:
    """
    평가 대기 중인 후보 조회 (배치 워커용).

    조건:
    - llm_status='pending'
    - llm_attempts < 3
    - similarity 범위 필터
    """
```

#### 3) update_candidate_score()
```python
async def update_candidate_score(
    self,
    candidate_id: int,
    llm_score: int,
    connection_reason: str
) -> dict:
    """후보 평가 결과 업데이트"""
```

#### 4) move_to_thought_pairs()
```python
async def move_to_thought_pairs(
    self,
    candidate_ids: List[int],
    min_score: int = 65
) -> int:
    """
    고득점 후보를 thought_pairs로 이동.

    Process:
    1. pair_candidates 조회 (score >= min_score)
    2. quality_tier 계산 (standard/premium/excellent)
    3. insert_thought_pairs_batch() 호출
    """
```

### 2.3 sampling.py (신규 모듈)

**파일**: `backend/services/sampling.py`

**클래스**: `SamplingStrategy`

```python
class SamplingStrategy:
    """초기 평가용 샘플링 전략"""

    def sample_initial(
        self,
        candidates: List[Dict[str, Any]],
        target_count: int = 100
    ) -> List[Dict[str, Any]]:
        """
        초기 100개 샘플 선택.

        Strategy:
        1. 유사도 구간별 분할 (Low/Mid/High)
           - Low (0.05-0.15): 40개 (창의적 조합)
           - Mid (0.15-0.25): 35개
           - High (0.25-0.35): 25개
        2. raw_note 다양성 고려 (Round-robin)
        """
```

### 2.4 batch_worker.py (신규 모듈)

**파일**: `backend/services/batch_worker.py`

**클래스**: `BatchEvaluationWorker`

```python
class BatchEvaluationWorker:
    """배치 평가 워커"""

    async def run_batch(self, max_candidates: int = 100) -> Dict[str, int]:
        """
        배치 평가 실행.

        Process:
        1. get_pending_candidates() 호출
        2. ThoughtPairCandidate 변환
        3. score_pairs() 호출 (10개씩)
        4. update_candidate_score() 호출
        5. 고득점(>=65)은 move_to_thought_pairs()

        Returns:
            {"evaluated": N, "migrated": M, "failed": F}
        """
```

### 2.5 recommendation.py (신규 모듈)

**파일**: `backend/services/recommendation.py`

**클래스**: `RecommendationEngine`

```python
class RecommendationEngine:
    """Essay 추천 엔진"""

    async def get_recommended_pairs(
        self,
        limit: int = 10,
        quality_tiers: List[str] = ["excellent", "premium", "standard"],
        diversity_weight: float = 0.3
    ) -> List[dict]:
        """
        추천 페어 조회.

        Algorithm:
        1. thought_pairs에서 미사용 + quality_tier 필터
        2. 다양성 스코어 계산 (raw_note 중복 페널티)
        3. 최종 점수 = claude_score × 0.7 + diversity × 0.3
        """
```

---

## Phase 3: API 엔드포인트

### 3.1 POST /pipeline/collect-candidates

**파일**: `backend/routers/pipeline.py`

```python
@router.post("/collect-candidates")
async def collect_candidates(
    min_similarity: float = Query(default=0.05),
    max_similarity: float = Query(default=0.35),
    top_k: int = Query(default=20)
):
    """
    전체 후보 수집 및 pair_candidates 저장.

    Process:
    1. find_candidate_pairs(limit=50000) 호출
    2. raw_note_id JOIN으로 추가
    3. insert_pair_candidates_batch() 저장

    Returns:
        {
            "total_candidates": 30000,
            "inserted": 28500,
            "duplicates": 1500
        }
    """
```

### 3.2 POST /pipeline/sample-initial

```python
@router.post("/sample-initial")
async def sample_initial(sample_size: int = Query(default=100)):
    """
    초기 100개 샘플 평가.

    Process:
    1. get_pending_candidates(limit=50000)
    2. SamplingStrategy.sample_initial(100)
    3. BatchEvaluationWorker.run_batch()

    Returns:
        {
            "sampled": 100,
            "evaluated": 98,
            "migrated": 45
        }
    """
```

### 3.3 POST /pipeline/score-candidates

```python
@router.post("/score-candidates")
async def score_candidates(
    max_candidates: int = Query(default=100),
    background_tasks: BackgroundTasks
):
    """
    배치 평가 (백그라운드).

    Returns:
        {"message": "Batch evaluation started"}
    """
```

### 3.4 GET /essays/recommended

```python
@router.get("/essays/recommended")
async def get_recommended_essays(
    limit: int = Query(default=10),
    quality_tiers: List[str] = Query(default=["excellent", "premium", "standard"])
):
    """
    AI 추천 Essay 후보 조회.

    Process:
    1. RecommendationEngine.get_recommended_pairs()
    2. thought_pairs에서 고품질 + 다양성 조합

    Returns:
        {"total": 10, "pairs": [...]}
    """
```

---

## Phase 4: 배치 워커 자동화

### 4.1 독립 실행 스크립트

**파일**: `backend/scripts/run_batch_worker.py`

```python
"""
Standalone batch worker.

Usage:
    python backend/scripts/run_batch_worker.py --max-candidates 100 --interval 300
"""

async def run_worker(max_candidates: int, interval: int):
    """
    배치 워커 실행.

    무한 루프:
    1. BatchEvaluationWorker.run_batch()
    2. sleep(interval)
    """
```

**실행 방법**:
```bash
# 5분마다 100개씩 평가
python backend/scripts/run_batch_worker.py --max-candidates 100 --interval 300

# 백그라운드 실행
nohup python backend/scripts/run_batch_worker.py > worker.log 2>&1 &
```

### 4.2 Cron 작업 (선택 사항)

```bash
# Crontab 추가
*/5 * * * * cd /path/to/backend && python scripts/run_batch_worker.py --interval 0
```

---

## Phase 5: 테스트

### 5.1 단위 테스트

**파일**: `backend/tests/unit/test_sampling.py`

```python
def test_sample_initial_basic():
    """100개 샘플링 검증"""

def test_sample_initial_diversity():
    """다양성 샘플링 검증 (균등 분포)"""
```

**파일**: `backend/tests/unit/test_batch_worker.py`

```python
@pytest.mark.asyncio
async def test_run_batch_success():
    """배치 평가 성공 케이스"""
```

### 5.2 통합 테스트

**파일**: `backend/tests/integration/test_full_pipeline.py`

```python
@pytest.mark.asyncio
async def test_hybrid_pipeline_end_to_end():
    """
    E2E 테스트:
    1. collect-candidates
    2. sample-initial
    3. score-candidates
    4. recommended
    """
```

### 5.3 성능 테스트

**파일**: `backend/tests/performance/test_batch_insert.py`

```python
async def test_insert_30k_candidates():
    """30,000개 저장 시간 < 3분 검증"""
```

---

## Phase 6: 문서 업데이트

### 6.1 CLAUDE.md 업데이트

**섹션 추가**:

```markdown
## Hybrid Strategy (Phase 3 Enhancement)

### Architecture
- pair_candidates: 전체 후보 보관 (30,000개)
- thought_pairs: 고품질만 저장 (100-300개)
- 배치 워커: 점진적 평가

### Performance
- 초기 평가: 100개 (20배 증가)
- 전체 활용: 30,000개 (100%)
- UI 응답: <100ms (변화 없음)
```

### 6.2 API 문서 업데이트

**새 엔드포인트**:
- POST /pipeline/collect-candidates
- POST /pipeline/sample-initial
- POST /pipeline/score-candidates
- GET /essays/recommended

---

## 예상 이슈 및 해결책

### 1. DB 트랜잭션 실패
**해결**: 배치별 독립 트랜잭션 (1000개씩), ON CONFLICT DO NOTHING

### 2. LLM 평가 실패
**해결**: llm_attempts < 3 재시도, Rate limiter 활용

### 3. 배치 워커 중복 실행
**해결**: 파일 기반 Lock (`/tmp/batch_worker.lock`) 또는 DB Lock

### 4. 메모리 부족 (30k 후보)
**해결**: 배치 처리 (1000개씩), Generator 패턴

### 5. UI 조회 성능 저하
**해결**: 인덱스 활용, 선택적 캐싱 (Redis)

---

## 마이그레이션 전략

### 단계적 배포

**Week 1**: DB 스키마 + 기본 CRUD
- Migration 006, 007 실행
- supabase_service.py 확장
- 단위 테스트

**Week 2**: 샘플링 + 배치 워커
- sampling.py, batch_worker.py 구현
- API 엔드포인트 2개
- 통합 테스트

**Week 3**: 추천 엔진 + UI
- recommendation.py 구현
- /essays/recommended 엔드포인트
- 프론트엔드 연동

**Week 4**: 자동화 + 모니터링
- run_batch_worker.py 스크립트
- Cron 설정
- 성능 테스트

### 롤백 계획

```sql
-- DB 롤백
ALTER TABLE thought_pairs DROP COLUMN claude_score;
DROP TABLE IF EXISTS pair_candidates CASCADE;
```

**코드 롤백**: Git revert (Phase별 커밋)

---

## Critical Files (우선순위 순)

### 1. backend/docs/supabase_migrations/006_create_pair_candidates.sql
- **이유**: 전체 후보 보관용 테이블 (전략의 핵심 인프라)

### 2. backend/services/supabase_service.py
- **이유**: 7개 신규 메서드 추가 (CRUD의 중심)

### 3. backend/services/batch_worker.py
- **이유**: 배치 평가 엔진 (99% 활용률의 핵심)

### 4. backend/routers/pipeline.py
- **이유**: 4개 신규 엔드포인트 추가

### 5. backend/schemas/zk.py
- **이유**: 4개 신규 Pydantic 모델 (타입 안전성)

---

## 성능 예측

| 지표 | 현재 | 하이브리드 C | 개선 |
|------|------|-------------|------|
| 후보 수집 | 60초+ | 5초 | 92% ↓ |
| 초기 평가 | 5개 | 100개 | 20배 ↑ |
| 전체 활용 | 5개 (0.02%) | 30,000개 (100%) | 6000배 ↑ |
| UI 응답 | <100ms | <100ms | 동일 |

**저장 공간**: +8MB (무시 가능)

---

## 검증 계획

### 초기 실행 (첫 1주)
1. `/pipeline/collect-candidates` → 30,000개 수집 확인
2. `/pipeline/sample-initial` → 100개 평가 확인
3. `thought_pairs` 테이블 → 30-45개 저장 확인

### 점진적 확장 (2-4주)
1. 배치 워커 실행 → 1일 500개씩 평가
2. `thought_pairs` 증가 → 200-300개 도달
3. `/essays/recommended` → 응답 시간 <100ms 확인

### 최종 검증 (1개월)
1. 전체 30,000개 평가 완료
2. Essay 생성 다양성 증가 확인
3. UI 응답 성능 유지 확인

---

## 완료 조건

- [ ] Phase 1: DB 마이그레이션 완료 (006, 007 실행)
- [ ] Phase 2: Python 서비스 레이어 구현 (5개 모듈)
- [ ] Phase 3: API 엔드포인트 구현 (4개 엔드포인트)
- [ ] Phase 4: 배치 워커 자동화 (스크립트 + Cron)
- [ ] Phase 5: 테스트 작성 및 통과 (단위/통합/성능)
- [ ] Phase 6: 문서 업데이트 (CLAUDE.md, API 문서)

**최종 목표**: 30,000개 후보를 100% 활용하면서도 UI 응답 속도는 유지하는 시스템 구축
