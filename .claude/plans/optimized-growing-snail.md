# Step 3 (ZK - 페어 선택) 구현 계획 - 알고리즘 수정 버전

## 목표
thought_units 간 **낮은 유사도 (서로 다른 아이디어)**를 계산하여 ZK "약한 연결" 페어를 찾고, Claude로 창의적 연결 가능성을 평가하여 thought_pairs 테이블에 저장

## ⚠️ 중요: 알고리즘 방향 전환
- **기존 문제**: 유사도 0.3-0.7 = 비슷한 아이디어 (같은 주제의 다른 각도)
- **수정 방향**: 유사도 0.05-0.35 = **서로 다른 아이디어** (예상 밖의 연결)
- **추가 제약**: 동일 출처(raw_note) 쌍 제외 → 서로 다른 메모에서만 연결
- **Claude 역할**: "억지 연결" 필터링 (threshold 기반)

## 현재 상태

### ✅ 완료된 작업
- **Step 1 (RAW)**: 5개 Notion 메모 import 완료
- **Step 2 (NORMALIZED)**: 11개 사고 단위 추출 + 임베딩 생성 완료
- **데이터베이스**: thought_units 테이블에 10개 데이터, 모두 embedding 있음
- **임베딩 모델**: text-embedding-3-small (1536 차원)

### 📊 데이터 현황
- **raw_notes**: 5개
- **thought_units**: 10개 (모두 embedding 생성됨)
- **가능한 쌍**: C(10,2) = **45개 쌍**
- **thought_pairs**: 0개 (Step 3 구현 대기)

## Step 3 알고리즘 (수정된 버전)

### 1. 유사도 계산 (pgvector) - 낮은 유사도 찾기
```
similarity = 1 - (embedding_a <=> embedding_b)
```
- pgvector의 `<=>` 연산자: cosine distance (0=동일, 2=정반대)
- **수정된 타겟 범위**: **0.05 ~ 0.35** (서로 다른 도메인의 아이디어)
- **추가 필터**: `a.raw_note_id != b.raw_note_id` (동일 출처 제외)
- 예상: 45개 쌍 중 10-20개가 범위 내 위치 (출처 제외 후 5-15개)

**유사도 의미 해석:**
- 0.05-0.15: 거의 무관한 주제 (억지 연결 가능성 높음)
- 0.15-0.25: 서로 다른 주제, 창의적 연결 가능
- 0.25-0.35: 약간 관련, 예상 밖 연결 가능
- 0.35+: 이미 유사한 주제 (ZK "weak ties" 목표에서 벗어남)

### 2. Claude 평가 - 창의적 연결 가능성
- 후보 쌍들을 Claude Sonnet 4.5에 전달
- 각 쌍의 **창의적 연결 가능성** 점수: 0-100
  - 0-40: 억지 연결, 무의미한 조합
  - 41-64: 연결 가능하나 평범함 (필터링 경계)
  - 65-85: 신선하고 흥미로운 연결 ← **threshold 기본값**
  - 86-100: 매우 창의적이고 통찰력 있는 연결
- 연결 이유(connection_reason) 생성
- **Threshold 필터링**: `score >= min_score` (기본 65)만 선택
- 상위 N개 쌍 선정 (기본 5개)

### 3. DB 저장
- thought_pairs 테이블에 저장:
  - `thought_a_id`, `thought_b_id` (a < b 보장, **서로 다른 raw_note**)
  - `similarity_score` (0.05-0.35, 낮을수록 서로 다름)
  - `connection_reason` (Claude 생성, 창의적 연결 이유)
  - `is_used_in_essay` (기본값 FALSE)

## 구현 계획

### Phase 1: Pydantic 스키마 생성

#### 파일 1: `backend/schemas/zk.py` (신규 생성)

**모델 정의:**
```python
class ThoughtPairCandidate(BaseModel):
    """유사도 계산 결과 (후보 쌍)"""
    thought_a_id: int
    thought_b_id: int
    thought_a_claim: str
    thought_b_claim: str
    similarity_score: float = Field(..., ge=0, le=1)

class PairScoringRequest(BaseModel):
    """Claude에게 보낼 평가 요청"""
    pairs: list[ThoughtPairCandidate] = Field(..., min_length=1, max_length=20)

class PairScore(BaseModel):
    """Claude가 반환하는 단일 쌍 점수"""
    thought_a_id: int
    thought_b_id: int
    logical_expansion_score: int = Field(..., ge=0, le=100)
    connection_reason: str = Field(..., min_length=10, max_length=300)

class PairScoringResult(BaseModel):
    """Claude 평가 결과 (여러 쌍)"""
    pair_scores: list[PairScore] = Field(..., min_length=1)

class ThoughtPairCreate(BaseModel):
    """DB 저장용 모델"""
    thought_a_id: int
    thought_b_id: int
    similarity_score: float = Field(..., ge=0, le=1)
    connection_reason: str = Field(..., max_length=500)

class ThoughtPairDB(ThoughtPairCreate):
    """DB 조회 모델"""
    id: int
    selected_at: datetime
    is_used_in_essay: bool = False

    model_config = {"from_attributes": True}
```

**라인 수 예상:** ~80 라인

#### 파일 2: `backend/schemas/essay.py` (신규 생성, Step 4 대비)

**모델 정의:**
```python
class UsedThought(BaseModel):
    """에세이에 사용된 사고 단위"""
    thought_id: int
    claim: str
    source_title: str
    source_url: str = Field(..., pattern=r'^https?://')

class EssayCreate(BaseModel):
    """에세이 생성 요청"""
    type: str = Field(default="essay")
    title: str = Field(..., min_length=5, max_length=100)
    outline: list[str] = Field(..., min_length=3, max_length=3)
    used_thoughts: list[UsedThought] = Field(..., min_length=1)
    reason: str = Field(..., max_length=300)
    pair_id: int

class EssayDB(EssayCreate):
    """DB 조회 모델"""
    id: int
    generated_at: datetime

    model_config = {"from_attributes": True}
```

**라인 수 예상:** ~50 라인

---

### Phase 2: Supabase Service 확장

**중요**: Stored Procedure 먼저 생성 필요!

#### Step 2.1: Stored Procedure 수정 (SQL) - 동일 출처 제외

`docs/supabase_setup.sql` 파일의 기존 `find_similar_pairs()` 함수를 수정:

```sql
-- Step 3: Stored Procedure for similarity search (수정 버전)
CREATE OR REPLACE FUNCTION find_similar_pairs(
    min_sim FLOAT DEFAULT 0.05,  -- 기본값 변경: 0.3 → 0.05
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
      AND a.raw_note_id != b.raw_note_id  -- ⭐ 추가: 동일 출처 제외
      AND (1 - (a.embedding <=> b.embedding)) BETWEEN min_sim AND max_sim
    ORDER BY similarity_score DESC
    LIMIT lim;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION find_similar_pairs IS 'Step 3: Find thought unit pairs from DIFFERENT sources within low similarity range (weak ties)';
```

**실행 방법:**
1. Supabase Dashboard → SQL Editor
2. 위 SQL 실행 (기존 함수를 덮어씀)
3. 성공 확인: `SELECT find_similar_pairs(0.05, 0.35, 5);`
4. **동일 출처 제외 확인**: 결과의 모든 쌍이 서로 다른 raw_note_id를 가져야 함

#### Step 2.2: `backend/services/supabase_service.py` (확장)

**새 메서드 추가:**

##### 1. `find_candidate_pairs()` - 낮은 유사도 페어 조회 (수정)
```python
async def find_candidate_pairs(
    self,
    min_similarity: float = 0.05,  # 기본값 변경: 0.3 → 0.05
    max_similarity: float = 0.35,  # 기본값 변경: 0.7 → 0.35
    limit: int = 20
) -> List[dict]:
    """
    pgvector로 낮은 유사도 범위 내 쌍 찾기 (Stored Procedure 호출).
    서로 다른 raw_note에서만 페어 선택 (동일 출처 제외).

    Args:
        min_similarity: 최소 유사도 (기본 0.05, 낮을수록 서로 다른 아이디어)
        max_similarity: 최대 유사도 (기본 0.35)
        limit: 최대 반환 개수 (기본 20)

    Returns:
        후보 쌍 목록 [
            {
                "thought_a_id": 1,
                "thought_b_id": 3,
                "thought_a_claim": "...",
                "thought_b_claim": "...",
                "similarity_score": 0.18  # 낮은 값 = 서로 다른 도메인
            }
        ]

    Raises:
        Exception: Stored Procedure 호출 실패 시
    """
    await self._ensure_initialized()

    try:
        response = await self.client.rpc(
            "find_similar_pairs",
            {
                "min_sim": min_similarity,
                "max_sim": max_similarity,
                "lim": limit
            }
        ).execute()

        candidates = response.data
        logger.info(f"Found {len(candidates)} candidate pairs from DIFFERENT sources (similarity {min_similarity}-{max_similarity})")
        return candidates

    except Exception as e:
        logger.error(f"Failed to find candidate pairs: {e}")
        # Stored Procedure 없으면 명확한 에러 메시지
        if "function find_similar_pairs" in str(e).lower():
            raise Exception(
                "Stored Procedure 'find_similar_pairs' not found. "
                "Please run docs/supabase_setup.sql first."
            )
        raise
```

##### 2. `insert_thought_pair()` - 단일 페어 저장
```python
async def insert_thought_pair(self, pair: ThoughtPairCreate) -> dict:
    """thought_pairs 테이블에 단일 페어 저장"""
```

##### 3. `insert_thought_pairs_batch()` - 배치 저장
```python
async def insert_thought_pairs_batch(self, pairs: List[ThoughtPairCreate]) -> List[dict]:
    """여러 페어 배치 저장 (UPSERT)"""
```

##### 4. `get_unused_thought_pairs()` - 미사용 페어 조회
```python
async def get_unused_thought_pairs(self, limit: int = 10) -> List[dict]:
    """is_used_in_essay = FALSE인 페어 조회 (Step 4용)"""
```

##### 5. `update_pair_used_status()` - 사용 상태 업데이트
```python
async def update_pair_used_status(self, pair_id: int, is_used: bool = True) -> dict:
    """에세이 생성 후 is_used_in_essay 업데이트"""
```

##### 6. `get_pair_with_thoughts()` - 페어 + 사고 단위 조회
```python
async def get_pair_with_thoughts(self, pair_id: int) -> dict:
    """
    페어 정보와 양쪽 사고 단위를 JOIN해서 조회.
    Step 4에서 에세이 생성 시 필요.
    """
```

**추가 라인 수 예상:** ~150 라인

---

### Phase 3: AI Service 확장

#### 파일: `backend/services/ai_service.py` (확장)

**새 메서드 추가:**

##### `score_pairs()` - 페어 평가 (프롬프트 재설계)
```python
async def score_pairs(
    self,
    candidates: List[ThoughtPairCandidate],
    top_n: int = 5
) -> PairScoringResult:
    """
    여러 후보 쌍의 창의적 연결 가능성 평가.

    Args:
        candidates: 낮은 유사도 범위 내 후보 쌍 목록 (서로 다른 도메인)
        top_n: 상위 몇 개를 선택할지 (사용 안 함, threshold 기반 필터링)

    Returns:
        PairScoringResult: 각 쌍의 점수 및 연결 이유
        주의: 모든 후보를 평가, threshold 필터링은 router에서 수행

    프롬프트:
    - System: "서로 다른 아이디어 간 창의적 연결 가능성을 평가하는 전문가"
    - User: 각 쌍의 claim을 제공하고 0-100 점수 요청
    - 억지 연결 감지 및 낮은 점수 부여
    - JSON 응답 요구

    Model: claude-sonnet-4-5-20250929
    Max tokens: 2000
    """
```

**프롬프트 설계 (재설계):**
```python
system_message = """당신은 서로 다른 도메인의 아이디어 간 창의적 연결 가능성을 평가하는 전문가입니다.

평가 대상 쌍들은 의도적으로 **유사도가 낮은** 조합입니다 (서로 다른 주제).
당신의 역할은 억지스럽거나 무의미한 연결을 걸러내고, 진정으로 신선하고 통찰력 있는 연결만 높은 점수를 주는 것입니다.

각 쌍에 대해:
1. 두 아이디어가 어떻게 창의적으로 연결될 수 있는지 분석
2. 창의적 연결 가능성 점수 (0-100) 부여
   - 0-40: 억지 연결, 무의미한 조합 (예: "커피" + "양자역학")
   - 41-64: 연결 가능하나 평범하거나 표면적 (예: "운동" + "건강")
   - 65-85: 신선하고 예상 밖의 연결 (예: "게임 난이도" + "교육 최적 도전")
   - 86-100: 매우 창의적이고 통찰력 있는 연결 (예: "정원 가꾸기" + "소프트웨어 리팩토링")
3. 연결 이유를 간결하게 (10-300자) 설명

중요 원칙:
- 단순 단어 유사성은 낮은 점수 (예: "일" + "직장" = 40점)
- 비유나 메타포로만 연결되면 중간 점수 (예: "산 등반" + "목표 달성" = 55점)
- 근본 원리나 구조의 유사성은 높은 점수 (예: "생태계 균형" + "경제 순환" = 78점)
- 전혀 무관한 조합은 매우 낮은 점수 (예: "아침식사" + "블랙홀" = 15점)"""

prompt = f"""다음 {len(candidates)}개의 사고 단위 쌍을 평가하세요.
각 쌍은 서로 다른 메모 출처에서 가져온 것으로, 유사도가 낮은 조합입니다.

{pairs_text}

JSON 형식으로 응답:
{{
  "pair_scores": [
    {{
      "thought_a_id": 1,
      "thought_b_id": 2,
      "logical_expansion_score": 72,
      "connection_reason": "A의 핵심 원리와 B의 구조는 ..."
    }}
  ]
}}

중요: connection_reason은 한 줄로 작성 (줄바꿈 금지), 10-300자.
JSON만 반환하세요."""
```

**추가 라인 수 예상:** ~100 라인

---

### Phase 4: Pipeline 라우터 확장

#### 파일: `backend/routers/pipeline.py` (확장)

**새 엔드포인트 추가:**

##### 1. `POST /pipeline/select-pairs` - Step 3 실행 (수정)
```python
@router.post("/select-pairs")
async def select_pairs(
    min_similarity: float = Query(default=0.05, ge=0, le=1, description="최소 유사도"),  # 변경: 0.3 → 0.05
    max_similarity: float = Query(default=0.35, ge=0, le=1, description="최대 유사도"),  # 변경: 0.7 → 0.35
    min_score: int = Query(default=65, ge=0, le=100, description="최소 창의적 연결 점수 (threshold)"),  # ⭐ 추가
    top_n: int = Query(default=5, ge=1, le=20, description="선택할 페어 개수"),
    supabase_service: SupabaseService = Depends(get_supabase_service),
    ai_service: AIService = Depends(get_ai_service),
):
    """
    Step 3: ZK 페어 선택 (낮은 유사도 + Claude 필터링)

    프로세스:
    1. find_candidate_pairs()로 낮은 유사도 범위 내 쌍 조회 (서로 다른 출처)
    2. 후보가 없으면 Fallback 전략 (범위 확대)
    3. ai_service.score_pairs()로 Claude 평가
    4. min_score 이상인 쌍만 필터링 (threshold)
    5. 점수 기준 정렬 및 상위 top_n개 선택
    6. insert_thought_pairs_batch()로 DB 저장
    7. 저장된 페어 개수 및 샘플 반환

    Args:
        min_similarity: 최소 유사도 (기본 0.05, 낮을수록 다른 아이디어)
        max_similarity: 최대 유사도 (기본 0.35)
        min_score: 최소 창의적 연결 점수 (기본 65, 사용자 조정 가능)
        top_n: 선택할 페어 개수 (기본 5)

    Returns:
        {
            "success": true,
            "candidates_found": 12,
            "candidates_after_threshold": 7,  # min_score 필터 후
            "pairs_selected": 5,
            "pairs": [
                {
                    "thought_a_id": 1,
                    "thought_b_id": 3,
                    "similarity": 0.18,  # 낮은 값 = 서로 다름
                    "score": 78,
                    "reason": "..."
                }
            ]
        }
    """
```

**처리 흐름 (수정):**
1. 유사도 범위 검증 (min < max)
2. Supabase에서 낮은 유사도 후보 쌍 조회 (서로 다른 raw_note)
3. 후보가 없으면 **Fallback 전략**:
   - 1차: 범위 0.05-0.35 (기본)
   - 2차: 범위 0.1-0.4 (확대)
   - 3차: 범위 0.15-0.45 (더 확대)
   - 모두 실패 시 에러 반환
4. Claude로 평가 (배치 처리)
5. **min_score 이상인 쌍만 필터링** (예: 65점 이상)
6. 점수 기준 정렬 및 상위 top_n개 선택
7. DB 저장 (UPSERT)
8. 결과 반환

##### 2. `GET /pipeline/pairs` - 저장된 페어 조회
```python
@router.get("/pairs")
async def get_pairs(
    only_unused: bool = Query(False),
    limit: int = Query(10, ge=1, le=100),
    supabase_service: SupabaseService = Depends(get_supabase_service),
):
    """저장된 thought_pairs 조회 (미사용/전체)"""
```

##### 3. `POST /pipeline/run-all` - 전체 파이프라인 (Step 1-3, 수정)
```python
@router.post("/run-all")
async def run_all_pipeline(
    page_size: int = Query(default=100, ge=1, le=100),
    min_similarity: float = Query(default=0.05, ge=0, le=1),  # 변경: 0.3 → 0.05
    max_similarity: float = Query(default=0.35, ge=0, le=1),  # 변경: 0.7 → 0.35
    min_score: int = Query(default=65, ge=0, le=100),  # ⭐ 추가
    top_n: int = Query(default=5, ge=1, le=20),
    ...
):
    """
    Step 1 → Step 2 → Step 3 순차 실행
    각 단계별 결과 반환

    주의: Step 3 파라미터가 수정됨 (낮은 유사도 + threshold)
    """
```

**추가 라인 수 예상:** ~200 라인

---

## 구현 파일 요약

| 파일 | 작업 | 예상 라인 수 |
|------|------|-------------|
| `backend/schemas/zk.py` | 신규 생성 | ~80 라인 |
| `backend/schemas/essay.py` | 신규 생성 (Step 4 대비) | ~50 라인 |
| `backend/services/supabase_service.py` | 6개 메서드 추가 | +150 라인 |
| `backend/services/ai_service.py` | 1개 메서드 추가 | +100 라인 |
| `backend/routers/pipeline.py` | 3개 엔드포인트 추가 | +200 라인 |
| **합계** | | **~580 라인** |

---

## 테스트 계획

### 1. 유사도 분포 확인 (실행 전)
```sql
-- 45개 쌍의 유사도 분포 확인
SELECT
    COUNT(*) as total_pairs,
    COUNT(CASE WHEN sim >= 0.3 AND sim <= 0.7 THEN 1 END) as weak_connections,
    MIN(sim) as min_similarity,
    MAX(sim) as max_similarity,
    AVG(sim) as avg_similarity
FROM (
    SELECT 1 - (a.embedding <=> b.embedding) as sim
    FROM thought_units a, thought_units b
    WHERE a.id < b.id
) subquery;
```

### 2. Step 3 실행 (수정된 파라미터)
```bash
# 기본값 사용 (낮은 유사도 0.05-0.35, threshold=65)
curl -X POST "http://localhost:8000/pipeline/select-pairs"

# 파라미터 조정 예시
curl -X POST "http://localhost:8000/pipeline/select-pairs?min_similarity=0.05&max_similarity=0.35&min_score=70&top_n=5"

# Threshold 낮추기 (더 많은 후보 허용)
curl -X POST "http://localhost:8000/pipeline/select-pairs?min_score=60"
```

### 3. 결과 검증
```sql
-- 저장된 페어 확인
SELECT
    tp.id,
    tp.thought_a_id,
    tp.thought_b_id,
    tp.similarity_score,
    tp.connection_reason,
    ta.claim as thought_a,
    tb.claim as thought_b
FROM thought_pairs tp
JOIN thought_units ta ON tp.thought_a_id = ta.id
JOIN thought_units tb ON tp.thought_b_id = tb.id
ORDER BY tp.similarity_score DESC;
```

---

## 성능 고려사항

### pgvector 인덱스
- 현재 10개 thought_units → 인덱스 불필요
- 1000+ 레코드 시 ivfflat 인덱스 생성 권장:
```sql
CREATE INDEX idx_thought_units_embedding
ON thought_units
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
```

### Claude API 호출 최적화
- 후보 쌍을 한 번에 배치 처리 (최대 20개)
- Rate limiting 적용 (5 req/sec)
- 토큰 사용량: ~500-1000 tokens per request

---

## 검증 스크립트 (임시 파일)

실행 후 다음 스크립트를 `temp/verification/`에 생성:

### `temp/verification/verify_step3.py`
- thought_pairs 테이블 데이터 확인
- 유사도 범위 검증
- connection_reason 품질 확인
- 상위 5개 페어 출력

### `temp/verification/analyze_similarity.py`
- 45개 쌍의 유사도 히스토그램 생성
- 0.3-0.7 범위 비율 분석
- 가장 유사한/먼 쌍 출력

---

## 예상 결과

### Step 3 실행 성공 시:
```json
{
  "success": true,
  "candidates_found": 12,
  "pairs_selected": 5,
  "pairs": [
    {
      "id": 1,
      "thought_a_id": 2,
      "thought_b_id": 5,
      "similarity_score": 0.52,
      "logical_expansion_score": 82,
      "connection_reason": "게임을 쉬면서 하는 것이 아니라는 생각과 일로 정의되는 정체성은, 노력의 본질과 자아실현의 연결고리를 제시할 수 있다."
    },
    ...
  ]
}
```

### thought_pairs 테이블:
- 5개 행 생성
- similarity_score: 0.3-0.7 범위
- connection_reason: Claude 생성 텍스트 (10-300자)
- is_used_in_essay: FALSE (Step 4 대기)

---

## 다음 단계: Step 4

Step 3 완료 후:
1. thought_pairs에서 미사용 페어 조회
2. Claude로 에세이 생성 (title, 3단 outline, reason)
3. essays 테이블 저장
4. thought_pairs.is_used_in_essay = TRUE 업데이트

---

## 파일 정리 규칙 (사용자 요청)

**앞으로 생성되는 임시 파일 관리:**
- 검증 스크립트 (.py) → `temp/verification/`
- 실험 결과 (.md) → `temp/experiments/`
- 백업 파일 (.sql) → `temp/experiments/`
- 사용 완료 후 정리

**핵심 문서:**
- DB 스키마 → `docs/`
- 검증 요약 → `docs/`
- README → `docs/`

---

## 실행 순서

1. **Phase 1**: schemas/zk.py, schemas/essay.py 생성
2. **Phase 2**: supabase_service.py 메서드 6개 추가
3. **Phase 3**: ai_service.py score_pairs() 메서드 추가
4. **Phase 4**: pipeline.py 엔드포인트 3개 추가
5. **테스트**: 유사도 분포 확인 → Step 3 실행 → 결과 검증
6. **문서화**: 검증 스크립트 temp/verification/에 생성

---

## 주의사항 및 특이 케이스

### 1. pgvector 관련
- **문법**: `<=>` 는 cosine distance (0=동일, 2=정반대)
- **NULL embedding**: thought_units에 embedding이 NULL인 레코드가 있으면 에러 발생
  - **대응**: `find_candidate_pairs()` 에서 `WHERE a.embedding IS NOT NULL AND b.embedding IS NOT NULL` 조건 추가
- **pgvector extension 미설치**: Supabase에서 extension 활성화 여부 확인 필요
  - **대응**: 에러 발생 시 명확한 메시지로 안내

### 2. 데이터 제약 조건
- **ordered_pair 제약**: `thought_a_id < thought_b_id` 보장 필요
  - **대응**: SQL 쿼리에서 `a.id < b.id` 조건으로 자동 보장
  - **추가 안전장치**: Python에서도 `min(id1, id2), max(id1, id2)` 로 정렬
- **UNIQUE 제약**: `(thought_a_id, thought_b_id)` 중복 저장 방지
  - **대응**: UPSERT 사용, 충돌 시 업데이트
- **동일 thought 쌍**: `thought_a_id = thought_b_id` 방지
  - **대응**: SQL에서 `a.id < b.id` 로 자동 방지 (등호 제외)

### 3. 유사도 계산 특이 케이스 (수정)
- **후보 쌍 0개**: 0.05-0.35 범위에 쌍이 없는 경우
  - **대응 1차**: Fallback 전략 (0.1-0.4 → 0.15-0.45 순차 확대)
  - **대응 2차**: 명확한 에러 메시지 + 전체 유사도 분포 통계 제공
  - **제안**: "메모를 더 추가하거나 범위를 조정하세요"
- **후보 쌍이 너무 많음**: 45개 모두가 0.05-0.35 범위인 경우 (거의 없음)
  - **대응**: LIMIT 파라미터로 상위 N개만 조회 (기본 20개)
- **유사도 1.0 (동일 임베딩)**: 동일한 claim으로 중복 생성된 경우
  - **대응**: 0.05-0.35 범위 필터로 자동 제외됨
- **동일 출처 쌍**: 같은 raw_note_id에서 나온 쌍
  - **대응**: Stored Procedure에서 `a.raw_note_id != b.raw_note_id` 조건으로 자동 제외
- **Threshold 필터 후 0개**: Claude 평가에서 모두 65점 미만인 경우
  - **대응**: 명확한 에러 메시지 + min_score 낮추기 제안 (예: 60점)
  - **로깅**: 필터링 전후 개수 기록 (candidates_found vs candidates_after_threshold)

### 4. Claude API 호출 관련
- **JSON 파싱 실패**: Claude가 ```json 마크다운 포함하는 경우
  - **대응**: Step 2와 동일한 전처리 로직 적용
  - 코드: `content.strip().removeprefix("```json").removeprefix("```").removesuffix("```")`
- **Claude가 점수 범위 위반**: logical_expansion_score가 0-100 밖인 경우
  - **대응**: Pydantic Field(..., ge=0, le=100) 검증으로 자동 에러
- **Claude가 일부 쌍만 반환**: 10개 요청했는데 8개만 반환
  - **대응**: 정상 처리, 반환된 것만 저장 (에러 아님)
- **connection_reason이 너무 김**: 300자 초과
  - **대응**: Pydantic Field(..., max_length=300) 검증
  - **추가 대응**: 프롬프트에 "10-300자" 명시
- **Claude API 실패**: Rate limit, 크레딧 부족, 네트워크 에러
  - **대응**: try-except로 잡고 명확한 에러 메시지 반환
  - **재시도 로직**: 선택적으로 추가 (일단 1회 시도만)

### 5. 배치 처리 특이 케이스
- **후보 쌍 20개 초과**: Claude에 한 번에 너무 많이 보내면 응답 품질 저하
  - **대응**: 최대 20개로 제한 (limit 파라미터)
  - **향후 개선**: 필요 시 10개씩 배치 분할 처리
- **top_n > 후보 개수**: 5개 요청했는데 후보가 3개만 있는 경우
  - **대응**: min(top_n, len(candidates)) 로 조정

### 6. DB 저장 특이 케이스
- **같은 쌍을 다시 저장**: 중복 실행 시 UNIQUE 제약 위반
  - **대응**: UPSERT (ON CONFLICT UPDATE) 사용
  - **결정**: 새로운 점수/이유로 업데이트 vs 기존 유지?
  - **권장**: 업데이트 (최신 Claude 평가 반영)
- **thought_units 삭제 후 재생성**: ID가 바뀌면 기존 pairs가 dangling reference
  - **대응**: DB 스키마에 ON DELETE CASCADE 설정되어 있음
- **is_used_in_essay가 이미 TRUE**: 재실행 시 기사용 페어 덮어쓰기 방지?
  - **대응**: UPSERT 시 is_used_in_essay는 업데이트하지 않도록 설정
  - **SQL**: `ON CONFLICT ... DO UPDATE SET ... (is_used_in_essay 제외)`

### 7. Step 1/2와의 일관성
- **비동기 패턴**: Step 1/2와 동일한 async/await 패턴 사용
- **에러 처리**: 동일한 try-except + HTTPException 구조
- **로깅**: logger.info/warning/error 일관성 유지
- **응답 포맷**: Step 1/2와 유사한 JSON 구조 (success, errors 등)

### 8. Supabase RPC 대신 SQL 실행
- **문제**: Supabase Python 클라이언트는 직접 SQL 실행 불가
- **해결**: PostgREST의 `.rpc()` 사용하거나, SQL을 테이블 연산으로 변환
- **대안 1**: Stored Procedure 생성 (supabase_setup.sql에 추가)
  ```sql
  CREATE OR REPLACE FUNCTION find_similar_pairs(
      min_sim FLOAT, max_sim FLOAT, lim INT
  ) RETURNS TABLE (...) AS $$
  BEGIN
      RETURN QUERY
      SELECT ... FROM thought_units a, thought_units b
      WHERE a.id < b.id AND ...;
  END;
  $$ LANGUAGE plpgsql;
  ```
- **대안 2**: Python에서 모든 thought_units 가져와 계산 (비효율)
- **권장**: **대안 1** (Stored Procedure 사용)

### 9. 타입 안전성
- **UUID vs str**: raw_note_id는 UUID 타입
  - **대응**: Pydantic 모델에서 UUID 타입 사용
- **float vs Decimal**: similarity_score는 Python float
  - **대응**: PostgreSQL FLOAT와 호환됨, 문제 없음
- **datetime 직렬화**: selected_at은 datetime
  - **대응**: model_dump(mode='json') 사용 (Step 2 패턴)

### 10. 프론트엔드 통합 대비
- **CORS 설정**: 이미 main.py에 설정되어 있음 (확인 완료)
- **응답 속도**: Claude 호출 + DB 쿼리 = 3-10초 예상
  - **대응**: 프론트엔드에 로딩 표시 필요 (구현 시 안내)
- **진행 상태 업데이트**: WebSocket 또는 SSE로 실시간 업데이트?
  - **일단 제외**: Step 3은 한 번에 완료 (배치 작음)

---

## 🔧 구현 시 체크리스트

### Phase 1 완료 조건
- [ ] schemas/zk.py에 모든 모델 정의 (6개 클래스)
- [ ] schemas/essay.py에 모든 모델 정의 (3개 클래스)
- [ ] datetime, UUID import 확인

### Phase 2 완료 조건
- [ ] Stored Procedure `find_similar_pairs()` 작성 (SQL)
- [ ] supabase_service.py에 6개 메서드 추가
- [ ] NULL embedding 필터링 조건 포함
- [ ] UPSERT ON CONFLICT 처리 (is_used_in_essay 제외)

### Phase 3 완료 조건
- [ ] ai_service.py의 score_pairs() 메서드
- [ ] JSON 파싱 전처리 (```json 제거)
- [ ] Pydantic 검증 에러 처리
- [ ] 프롬프트 명확성 (점수 기준, 길이 제한)

### Phase 4 완료 조건
- [ ] /pipeline/select-pairs 엔드포인트
- [ ] min < max 검증
- [ ] 후보 0개 에러 처리 + 힌트 제공
- [ ] top_n vs 후보 개수 조정
- [ ] /pipeline/pairs GET 엔드포인트
- [ ] /pipeline/run-all 엔드포인트 (선택)

### 테스트 완료 조건
- [ ] 유사도 분포 확인 쿼리 실행
- [ ] Step 3 실행 (curl 또는 Swagger UI)
- [ ] thought_pairs 테이블 데이터 검증
- [ ] 재실행 테스트 (UPSERT 동작 확인)
- [ ] 에러 케이스 테스트 (후보 0개, Claude 실패 등)

### 문서화 완료 조건
- [ ] temp/verification/verify_step3.py 생성
- [ ] temp/verification/analyze_similarity.py 생성
- [ ] docs/VERIFICATION_SUMMARY.md 업데이트 (Step 3 섹션 추가)

---

## ⚠️ 치명적 에러 방지 규칙

1. **pgvector extension 확인**: 첫 실행 전 Supabase에서 `CREATE EXTENSION vector` 실행 확인
2. **Stored Procedure 먼저 생성**: find_similar_pairs() 없으면 Step 3 실행 불가
3. **embedding NULL 체크**: 필수! 없으면 pgvector 연산 에러
4. **ordered_pair 보장**: a.id < b.id 조건 누락 시 중복 쌍 생성
5. **UPSERT 설정**: ON CONFLICT 없으면 UNIQUE 제약 위반 에러

---

## 🔄 알고리즘 변경 요약

### 변경 전 (문제 있던 방식)
| 요소 | 기존 값 | 문제점 |
|------|---------|--------|
| 유사도 범위 | 0.3 - 0.7 | 비슷한 주제 선택 (같은 주제의 다른 각도) |
| 출처 제약 | 없음 | 같은 메모 내 쌍 연결 가능 |
| Claude 역할 | 논리적 확장 | 이미 유사한 아이디어 연결 |
| 필터링 | 점수 정렬만 | 억지 연결 걸러내기 어려움 |

### 변경 후 (수정된 방식)
| 요소 | 수정 값 | 해결 방법 |
|------|---------|----------|
| 유사도 범위 | **0.05 - 0.35** | 서로 다른 도메인 선택 |
| 출처 제약 | `raw_note_id != raw_note_id` | 서로 다른 메모에서만 연결 |
| Claude 역할 | **창의적 연결 가능성** | 예상 밖 통찰 평가 |
| 필터링 | **threshold (min_score=65)** | 억지 연결 자동 제거 |

### Threshold 파라미터 설명
- **타입**: API Query 파라미터 (사용자 조정 가능)
- **기본값**: 65점 (신선하고 흥미로운 연결)
- **범위**: 0-100
- **역할**: Claude 평가 후 점수 필터링
- **사용법**:
  ```bash
  # 기본값 사용
  POST /pipeline/select-pairs

  # 더 엄격하게 (높은 품질)
  POST /pipeline/select-pairs?min_score=75

  # 더 너그럽게 (더 많은 후보)
  POST /pipeline/select-pairs?min_score=55
  ```
- **초기 캘리브레이션**: 첫 실행 후 결과 검토 → 필요 시 조정 (1회)
- **이후 사용**: 조정한 값으로 계속 자동 실행 (재조정 선택적)

### 예상 효과
1. **동일 출처 쌍 제거**: "게임..." 메모 내 Thought 8↔9 같은 쌍 자동 배제
2. **서로 다른 아이디어 연결**: 유사도 0.18 = 게임+교육, 정원+소프트웨어 등
3. **억지 연결 필터링**: "아침식사+블랙홀" (15점) 자동 제거
4. **창의적 통찰 발굴**: "생태계 균형+경제 순환" (78점) 선택

---

## 🆕 수정된 파일 및 변경 내역

### 1. `backend/docs/supabase_setup.sql`
- **변경**: `find_similar_pairs()` 함수 수정
- **추가 조건**: `AND a.raw_note_id != b.raw_note_id`
- **기본값**: min_sim=0.05, max_sim=0.35

### 2. `backend/services/supabase_service.py`
- **변경**: `find_candidate_pairs()` 메서드 기본값
- **추가 로직**: 동일 출처 제외 확인

### 3. `backend/services/ai_service.py`
- **변경**: `score_pairs()` 프롬프트 전면 재설계
- **새 평가 기준**: 창의적 연결 가능성 (억지 연결 감지)

### 4. `backend/routers/pipeline.py`
- **변경**: `/pipeline/select-pairs` 엔드포인트
- **추가 파라미터**: `min_score` (threshold)
- **추가 로직**: Fallback 전략, threshold 필터링
- **기본값**: min_similarity=0.05, max_similarity=0.35, min_score=65

### 5. `backend/schemas/zk.py`
- **변경 없음** (기존 스키마 그대로 사용 가능)
- **주의**: `logical_expansion_score` 필드명 유지 (실제로는 "창의적 연결 점수"로 해석)

---

## 📌 MVP 이후 개선 사항

### 다중 Pairs 지원 (MVP+1)

**현재 (MVP)**: 1개의 pair (2개의 thought_unit)만 연결하여 에세이 생성

**개선 방향**: 여러 pairs를 동시에 활용하여 더 풍부한 글감 생성

#### 구현 아이디어

##### 1. 데이터 구조 변경
```sql
-- essays 테이블 확장
ALTER TABLE essays ADD COLUMN pair_ids INTEGER[] DEFAULT '{}';
-- 기존: pair_id INTEGER (단일)
-- 변경: pair_ids INTEGER[] (배열)

-- 또는 연결 테이블 생성
CREATE TABLE essay_pairs (
    essay_id INTEGER REFERENCES essays(id),
    pair_id INTEGER REFERENCES thought_pairs(id),
    sequence_order INTEGER,  -- 글 내 사용 순서
    PRIMARY KEY (essay_id, pair_id)
);
```

##### 2. API 파라미터 추가
```python
@router.post("/generate-essays")
async def generate_essays(
    pair_count: int = Query(default=1, ge=1, le=5, description="사용할 페어 개수"),
    # pair_count=1: MVP (현재)
    # pair_count=2-3: 다중 관점 에세이
    # pair_count=4-5: 복합 주제 탐구
    ...
):
```

##### 3. Claude 프롬프트 확장
```python
# 단일 pair (MVP)
system_message = """2개의 사고 단위를 연결하여 글감을 생성하세요."""

# 다중 pairs (MVP+1)
system_message = """다음 {pair_count}개의 사고 단위 쌍들을 모두 활용하여
하나의 통합된 글감을 생성하세요.

각 쌍의 연결 관계를 고려하면서, 전체적으로 일관된 주제와 흐름을 구성하세요.
- 2-3개 쌍: 다각도 분석, 대조/비교
- 4-5개 쌍: 종합적 탐구, 체계적 전개
"""
```

##### 4. 사용 시나리오

**시나리오 A: 대조적 관점 (2 pairs)**
- Pair 1: "게임 난이도" ↔ "교육 최적 도전" (유사도 0.18)
- Pair 2: "몰입 경험" ↔ "업무 생산성" (유사도 0.22)
- → 글감: "학습과 업무에서의 최적 난이도 설계 원칙"

**시나리오 B: 다층적 분석 (3 pairs)**
- Pair 1: "정원 가꾸기" ↔ "소프트웨어 리팩토링" (유사도 0.15)
- Pair 2: "생태계 균형" ↔ "조직 문화" (유사도 0.19)
- Pair 3: "장기 투자" ↔ "기술 부채" (유사도 0.21)
- → 글감: "지속 가능한 성장의 공통 원리: 자연, 코드, 조직"

**시나리오 C: 종합적 탐구 (5 pairs)**
- 5개의 서로 다른 도메인 페어를 연결
- → 글감: "복잡계 이론으로 바라본 창의성의 본질"

##### 5. 구현 우선순위

**Phase 1 (MVP+1 초기)**
- 2-3 pairs 지원 (가장 수요 높음)
- essay_pairs 연결 테이블 생성
- generate_essays 엔드포인트에 pair_count 파라미터 추가
- Claude 프롬프트 템플릿 확장

**Phase 2 (MVP+2)**
- 4-5 pairs 지원 (복잡한 글감)
- Pair 간 연결성 자동 분석 (graph-based)
- 최적 조합 추천 알고리즘

**Phase 3 (MVP+3)**
- 사용자 정의 pair 조합 선택 UI
- Pair 배치 순서 최적화
- 글감 복잡도 메트릭 제공

#### 기술적 고려사항

**Claude API 토큰 제한**
- 1 pair: ~500 tokens input
- 5 pairs: ~2500 tokens input
- 대응: 배치 크기 조정, 요약 전처리

**DB 쿼리 성능**
- 다중 pair 조회 시 JOIN 최적화
- 인덱스: `(is_used_in_essay, similarity_score)`

**사용자 경험**
- 기본값은 1 pair (단순함 유지)
- 고급 사용자에게만 다중 pair 옵션 노출
- 복잡도 경고: "3개 이상 pair는 글 구성이 어려울 수 있습니다"

#### 예상 효과

**장점**
1. 더 풍부하고 다층적인 글감 생성
2. 여러 도메인 지식의 융합
3. 창의적 통찰의 폭 확대

**단점**
1. 글감 복잡도 증가 (초보자 진입장벽)
2. Claude 비용 증가 (토큰 사용량 2-5배)
3. 글 일관성 유지 어려움

**권장 사용법**
- 기본: 1 pair (MVP)
- 중급: 2-3 pairs (다각도 분석)
- 고급: 4-5 pairs (복합 주제 탐구)

---

**Step 3 완료! 다음은 Step 4 구현입니다.**

---
---

# Step 4 (Essay 생성) 구현 계획

## 목표
thought_pairs에서 미사용 페어를 조회하고, Claude로 에세이 글감(title, 3단 outline, reason)을 생성하여 essays 테이블에 저장

## 현재 상태

### ✅ Step 3 완료 상태
- **thought_pairs**: 10개 저장됨 (ID 1-10)
- **모든 페어**: is_used_in_essay = FALSE (미사용 상태)
- **유사도 범위**: 0.29-0.34 (낮은 유사도, 서로 다른 도메인)
- **창의적 연결 점수**: 71-76점 (threshold 65 통과)
- **동일 출처 제외**: 모든 페어가 서로 다른 raw_note에서 생성됨

### 📊 현재 데이터 현황
- **raw_notes**: 5개
- **thought_units**: 11개 (모두 embedding 생성됨)
- **thought_pairs**: 10개 (모두 is_used_in_essay = FALSE)
- **essays**: 0개 (Step 4 구현 대기)

### ✅ 이미 완료된 작업
1. **schemas/essay.py**: 모든 Pydantic 모델 완성 (71 라인)
   - UsedThought, EssayCreate, EssayDB, EssayResponse, EssayListResponse
2. **supabase_service.py의 get_pair_with_thoughts()**: 완성됨 (Lines 520-621)
   - 페어 + 양쪽 thought_units + raw_notes JOIN 조회
   - Step 4에서 즉시 사용 가능

## Step 4 알고리즘

### 1. 미사용 페어 조회
- `get_unused_thought_pairs(limit=10)` 사용
- is_used_in_essay = FALSE 조건
- 기본적으로 최대 10개 조회 (사용자 조정 가능)

### 2. 각 페어에 대해 에세이 생성
- `get_pair_with_thoughts(pair_id)` 호출하여 전체 정보 가져오기
  - thought_a (claim, context, source_title, source_url)
  - thought_b (claim, context, source_title, source_url)
  - similarity_score, connection_reason
- Claude Sonnet 4.5에 전달:
  - **Input**: 2개의 사고 단위 (claim + context + 출처)
  - **Output**: Essay (title, outline[3], reason)
- Pydantic 검증 (EssayCreate 모델)

### 3. DB 저장 및 상태 업데이트
- essays 테이블에 저장 (JSONB 직렬화)
- thought_pairs.is_used_in_essay = TRUE 업데이트
- CASCADE delete 보장 (pair 삭제 시 essay도 삭제)

---

## 전제 조건: essays 테이블 생성 확인

### ✅ DB 스키마 확인 완료
`backend/docs/supabase_setup.sql` (Lines 57-68)에 essays 테이블이 이미 정의되어 있습니다:

```sql
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
```

### 테이블 생성 확인 방법
Step 4 구현 전에 Supabase에서 essays 테이블이 생성되었는지 확인:

```sql
-- 테이블 존재 확인
SELECT EXISTS (
    SELECT FROM information_schema.tables
    WHERE table_name = 'essays'
);

-- 테이블 구조 확인
\d essays

-- 또는 컬럼 정보 확인
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'essays'
ORDER BY ordinal_position;
```

**만약 테이블이 없다면:**
1. Supabase Dashboard → SQL Editor
2. `backend/docs/supabase_setup.sql` 전체 내용 복사
3. 실행 (CREATE TABLE IF NOT EXISTS이므로 중복 생성 걱정 없음)

---

## 구현 계획

### Phase 1: AI Service 확장

#### 파일: `backend/services/ai_service.py` (확장)

**새 메서드 추가:**

##### `generate_essay()` - 에세이 글감 생성
```python
async def generate_essay(
    self,
    pair_data: dict,
) -> dict:
    """
    단일 페어로부터 에세이 글감 생성.

    Args:
        pair_data: get_pair_with_thoughts() 결과
            {
                "pair_id": int,
                "similarity_score": float,
                "connection_reason": str,
                "thought_a": {
                    "id": int,
                    "claim": str,
                    "context": str | None,
                    "source_title": str,
                    "source_url": str
                },
                "thought_b": { ... }
            }

    Returns:
        {
            "title": str,  # 5-100자
            "outline": [str, str, str],  # 정확히 3개
            "reason": str,  # 최대 300자
            "used_thoughts": [
                {
                    "thought_id": int,
                    "claim": str,
                    "source_title": str,
                    "source_url": str
                }
            ]
        }

    Raises:
        ValueError: Claude 응답 파싱 실패
        ValidationError: Pydantic 검증 실패
    """
```

**프롬프트 설계:**
```python
system_message = """당신은 창의적인 글감을 만드는 전문가입니다.

두 개의 서로 다른 사고 단위(thought unit)가 주어졌을 때, 이들을 연결하여 신선하고 흥미로운 글감(essay prompt)을 생성하세요.

출력 형식:
1. **제목 (title)**: 글감의 핵심을 담은 제목 (5-100자)
   - 두 아이디어의 연결을 암시하되, 너무 직설적이지 않게
   - 호기심을 자극하는 제목

2. **3단 개요 (outline)**: 글의 구조를 나타내는 3개 문장
   - 1단: 첫 번째 사고 단위 소개 또는 배경 설정
   - 2단: 두 번째 사고 단위 도입 및 연결점 탐색
   - 3단: 통합된 통찰 또는 새로운 질문 제시
   - 각 문장은 50-200자

3. **이 조합을 선택한 이유 (reason)**: 왜 이 두 아이디어를 연결하면 흥미로운 글이 나올지 설명 (50-300자)
   - 독자가 얻을 수 있는 새로운 시각
   - 두 도메인의 의외의 연결점

중요 원칙:
- 억지 연결 지양: 자연스러운 흐름 유지
- 구체적 예시: 추상적 개념만 나열하지 말고 구체적 상황 제시
- 독자 중심: 실제로 읽고 싶은 글감인지 고려
"""

prompt = f"""다음 두 사고 단위를 바탕으로 글감을 생성하세요.

**Thought A** (출처: {source_title_a})
- Claim: {claim_a}
- Context: {context_a or "없음"}
- 출처 URL: {source_url_a}

**Thought B** (출처: {source_title_b})
- Claim: {claim_b}
- Context: {context_b or "없음"}
- 출처 URL: {source_url_b}

**두 아이디어의 연결 이유** (Step 3에서 평가):
{connection_reason}

**유사도**: {similarity_score:.3f} (낮은 값 = 서로 다른 도메인)

---

JSON 형식으로 응답:
{{
  "title": "글감 제목 (5-100자)",
  "outline": [
    "1단: 첫 번째 아이디어 소개...",
    "2단: 두 번째 아이디어와 연결...",
    "3단: 통합된 통찰..."
  ],
  "reason": "이 조합을 선택한 이유 (50-300자)"
}}

중요:
- outline은 정확히 3개 문장
- reason은 한 줄로 작성 (줄바꿈 금지)
- JSON만 반환
"""
```

**구현 패턴** (기존 메서드 참고):
1. `generate_content_with_claude()` 호출
2. `safe_json_parse()` 로 응답 파싱
3. Pydantic 검증 (EssayCreate 모델)
4. used_thoughts 리스트 생성
5. 딕셔너리 반환

**에러 처리**:
- JSON 파싱 실패 → ValueError with raw content
- Pydantic 검증 실패 → ValidationError with details
- Claude API 실패 → Exception with error message

**추가 라인 수 예상:** ~120 라인

---

### Phase 2: Supabase Service 확장

#### 파일: `backend/services/supabase_service.py` (확장)

**새 메서드 추가:**

##### 1. `insert_essay()` - 단일 에세이 저장
```python
async def insert_essay(self, essay: EssayCreate) -> dict:
    """
    essays 테이블에 단일 에세이 저장.

    Args:
        essay: EssayCreate 모델 인스턴스

    Returns:
        {
            "id": int,
            "type": str,
            "title": str,
            "outline": list[str],
            "used_thoughts_json": list[dict],
            "reason": str,
            "pair_id": int,
            "generated_at": str (ISO format)
        }

    Raises:
        Exception: DB 저장 실패 시
    """
    await self._ensure_initialized()

    try:
        # JSONB 필드 직렬화
        essay_dict = {
            "type": essay.type,
            "title": essay.title,
            "outline": essay.outline,  # list → JSONB
            "used_thoughts_json": [t.model_dump() for t in essay.used_thoughts],  # JSONB
            "reason": essay.reason,
            "pair_id": essay.pair_id
        }

        response = await self.client.table("essays")\
            .insert(essay_dict)\
            .execute()

        inserted = response.data[0]
        logger.info(f"Inserted essay ID {inserted['id']} for pair {essay.pair_id}")
        return inserted

    except Exception as e:
        logger.error(f"Failed to insert essay: {e}")
        raise
```

##### 2. `insert_essays_batch()` - 배치 저장
```python
async def insert_essays_batch(self, essays: List[EssayCreate]) -> List[dict]:
    """
    여러 에세이 배치 저장.

    Args:
        essays: EssayCreate 모델 리스트

    Returns:
        저장된 에세이 리스트

    Note:
        - UPSERT는 하지 않음 (중복 방지는 pair_id 외래키로 보장)
        - 실패 시 전체 롤백
    """
    await self._ensure_initialized()

    if not essays:
        return []

    try:
        essays_dict = [
            {
                "type": e.type,
                "title": e.title,
                "outline": e.outline,
                "used_thoughts_json": [t.model_dump() for t in e.used_thoughts],
                "reason": e.reason,
                "pair_id": e.pair_id
            }
            for e in essays
        ]

        response = await self.client.table("essays")\
            .insert(essays_dict)\
            .execute()

        inserted = response.data
        logger.info(f"Batch inserted {len(inserted)} essays")
        return inserted

    except Exception as e:
        logger.error(f"Failed to batch insert essays: {e}")
        raise
```

##### 3. `get_essays()` - 에세이 목록 조회
```python
async def get_essays(
    self,
    limit: int = 10,
    offset: int = 0,
    order_by: str = "generated_at.desc"
) -> List[dict]:
    """
    essays 테이블 조회 (최신순).

    Args:
        limit: 최대 반환 개수 (기본 10)
        offset: 건너뛸 개수 (페이지네이션)
        order_by: 정렬 기준 (기본 "generated_at.desc")

    Returns:
        에세이 리스트 (JSONB 필드 자동 파싱됨)
    """
    await self._ensure_initialized()

    try:
        response = await self.client.table("essays")\
            .select("*")\
            .order(order_by)\
            .limit(limit)\
            .offset(offset)\
            .execute()

        essays = response.data
        logger.info(f"Retrieved {len(essays)} essays")
        return essays

    except Exception as e:
        logger.error(f"Failed to get essays: {e}")
        raise
```

##### 4. `get_essay_by_id()` - 단일 에세이 조회
```python
async def get_essay_by_id(self, essay_id: int) -> dict:
    """
    단일 에세이 조회.

    Args:
        essay_id: 에세이 ID

    Returns:
        에세이 데이터

    Raises:
        Exception: 에세이가 없거나 조회 실패 시
    """
    await self._ensure_initialized()

    try:
        response = await self.client.table("essays")\
            .select("*")\
            .eq("id", essay_id)\
            .single()\
            .execute()

        essay = response.data
        logger.info(f"Retrieved essay ID {essay_id}")
        return essay

    except Exception as e:
        logger.error(f"Failed to get essay {essay_id}: {e}")
        raise
```

**추가 라인 수 예상:** ~150 라인

---

### Phase 3: Pipeline 라우터 확장

#### 파일: `backend/routers/pipeline.py` (확장)

**새 엔드포인트 추가:**

##### 1. `POST /pipeline/generate-essays` - Step 4 실행
```python
@router.post("/generate-essays")
async def generate_essays(
    max_pairs: int = Query(default=5, ge=1, le=10, description="처리할 최대 페어 개수"),
    supabase_service: SupabaseService = Depends(get_supabase_service),
    ai_service: AIService = Depends(get_ai_service),
):
    """
    Step 4: Essay 글감 생성

    프로세스:
    1. get_unused_thought_pairs()로 미사용 페어 조회
    2. 각 페어에 대해:
       a. get_pair_with_thoughts()로 전체 정보 가져오기
       b. ai_service.generate_essay()로 Claude 호출
       c. 에세이 생성 (title, outline, reason)
    3. insert_essays_batch()로 배치 저장
    4. 각 페어의 is_used_in_essay = TRUE 업데이트
    5. 생성된 에세이 목록 반환

    Args:
        max_pairs: 처리할 최대 페어 개수 (기본 5, 최대 10)

    Returns:
        {
            "success": true,
            "pairs_processed": 5,
            "essays_generated": 5,
            "essays": [
                {
                    "id": 1,
                    "title": "...",
                    "outline": ["...", "...", "..."],
                    "reason": "...",
                    "pair_id": 1,
                    "used_thoughts": [...]
                }
            ],
            "errors": []
        }

    Note:
        - 부분 성공 허용: 일부 페어 실패해도 성공한 것은 저장
        - 각 페어는 독립적으로 처리 (한 페어 실패가 다른 페어에 영향 없음)
    """
    result = {
        "success": False,
        "pairs_processed": 0,
        "essays_generated": 0,
        "essays": [],
        "errors": [],
    }

    try:
        # 1. 미사용 페어 조회
        logger.info(f"Step 4: Fetching up to {max_pairs} unused pairs...")
        unused_pairs = await supabase_service.get_unused_thought_pairs(limit=max_pairs)

        if not unused_pairs:
            logger.warning("No unused pairs found")
            result["errors"].append("No unused pairs available. Run Step 3 first.")
            return result

        logger.info(f"Found {len(unused_pairs)} unused pairs")

        # 2. 각 페어에 대해 에세이 생성
        generated_essays: List[EssayCreate] = []
        processed_pair_ids: List[int] = []

        for pair in unused_pairs:
            pair_id = pair["id"]
            try:
                result["pairs_processed"] += 1

                # 2a. 페어 전체 정보 가져오기
                pair_data = await supabase_service.get_pair_with_thoughts(pair_id)

                # 2b. Claude로 에세이 생성
                logger.info(f"Generating essay for pair {pair_id}...")
                essay_dict = await ai_service.generate_essay(pair_data)

                # 2c. EssayCreate 모델 생성
                essay = EssayCreate(
                    title=essay_dict["title"],
                    outline=essay_dict["outline"],
                    used_thoughts=essay_dict["used_thoughts"],
                    reason=essay_dict["reason"],
                    pair_id=pair_id
                )

                generated_essays.append(essay)
                processed_pair_ids.append(pair_id)
                logger.info(f"✓ Essay generated for pair {pair_id}: {essay.title[:50]}...")

            except Exception as e:
                error_msg = f"Failed to generate essay for pair {pair_id}: {str(e)}"
                logger.error(error_msg)
                result["errors"].append(error_msg)
                # 계속 진행 (부분 성공 허용)

        # 3. 생성된 에세이 배치 저장
        if generated_essays:
            logger.info(f"Saving {len(generated_essays)} essays to DB...")
            saved_essays = await supabase_service.insert_essays_batch(generated_essays)
            result["essays_generated"] = len(saved_essays)
            result["essays"] = saved_essays

            # 4. 사용된 페어 상태 업데이트
            logger.info("Updating pair usage status...")
            for pair_id in processed_pair_ids:
                try:
                    await supabase_service.update_pair_used_status(pair_id, is_used=True)
                except Exception as e:
                    logger.error(f"Failed to update pair {pair_id} status: {e}")
                    # 에러 무시 (에세이는 이미 저장됨)

            logger.info(f"✓ Step 4 completed: {len(saved_essays)} essays generated")
            result["success"] = True
        else:
            logger.warning("No essays were successfully generated")
            result["errors"].append("All essay generation attempts failed")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Step 4 failed: {e}", exc_info=True)
        result["errors"].append(f"Pipeline error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

    return result
```

##### 2. `GET /pipeline/essays` - 에세이 목록 조회
```python
@router.get("/essays")
async def get_essays_list(
    limit: int = Query(default=10, ge=1, le=100, description="최대 반환 개수"),
    offset: int = Query(default=0, ge=0, description="건너뛸 개수"),
    supabase_service: SupabaseService = Depends(get_supabase_service),
):
    """
    저장된 에세이 목록 조회 (최신순).

    Args:
        limit: 최대 반환 개수 (기본 10)
        offset: 건너뛸 개수 (페이지네이션)

    Returns:
        {
            "total": int,
            "essays": [...]
        }
    """
    try:
        essays = await supabase_service.get_essays(limit=limit, offset=offset)

        # TODO: total count 쿼리 추가 (현재는 반환된 개수로 대체)
        return {
            "total": len(essays),
            "essays": essays
        }

    except Exception as e:
        logger.error(f"Failed to get essays: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

##### 3. `POST /pipeline/run-all` - 전체 파이프라인 (Step 1-4, 확장)
```python
@router.post("/run-all")
async def run_all_pipeline(
    # Step 1 params
    page_size: int = Query(default=100, ge=1, le=100),
    # Step 3 params
    min_similarity: float = Query(default=0.05, ge=0, le=1),
    max_similarity: float = Query(default=0.35, ge=0, le=1),
    min_score: int = Query(default=65, ge=0, le=100),
    top_n: int = Query(default=5, ge=1, le=20),
    # Step 4 params (새로 추가)
    max_essay_pairs: int = Query(default=5, ge=1, le=10, description="에세이 생성할 페어 개수"),
    supabase_service: SupabaseService = Depends(get_supabase_service),
    notion_service: NotionService = Depends(get_notion_service),
    ai_service: AIService = Depends(get_ai_service),
):
    """
    전체 파이프라인 실행: Step 1 → Step 2 → Step 3 → Step 4

    Returns:
        {
            "success": bool,
            "step1_imported": int,
            "step2_thoughts": int,
            "step3_pairs": int,
            "step4_essays": int,  # 새로 추가
            "errors": [...]
        }
    """
    result = {
        "success": False,
        "step1_imported": 0,
        "step2_thoughts": 0,
        "step3_pairs": 0,
        "step4_essays": 0,  # 새로 추가
        "errors": [],
    }

    try:
        # Step 1-3 (기존 로직 유지)
        # ...

        # Step 4: Essay 생성 (새로 추가)
        logger.info("Starting Step 4: Essay generation...")
        essay_result = await generate_essays(
            max_pairs=max_essay_pairs,
            supabase_service=supabase_service,
            ai_service=ai_service
        )

        result["step4_essays"] = essay_result["essays_generated"]
        result["errors"].extend(essay_result["errors"])

        # 전체 성공 판단
        if result["step4_essays"] > 0:
            result["success"] = True
            logger.info(f"✓ Full pipeline completed: {result['step4_essays']} essays generated")
        else:
            logger.warning("Pipeline completed but no essays generated")

    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        result["errors"].append(f"Pipeline error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

    return result
```

**추가 라인 수 예상:** ~250 라인

---

## 구현 파일 요약

| 파일 | 작업 | 예상 라인 수 |
|------|------|-------------|
| `backend/schemas/essay.py` | ✅ 이미 완성 | 0 라인 (변경 없음) |
| `backend/services/ai_service.py` | 1개 메서드 추가 (generate_essay) | +120 라인 |
| `backend/services/supabase_service.py` | 4개 메서드 추가 (essay CRUD) | +150 라인 |
| `backend/routers/pipeline.py` | 2개 엔드포인트 추가, 1개 확장 | +250 라인 |
| **합계** | | **~520 라인** |

---

## 테스트 계획

### 1. 미사용 페어 확인 (실행 전)
```sql
-- 현재 미사용 페어 상태
SELECT
    id,
    thought_a_id,
    thought_b_id,
    similarity_score,
    is_used_in_essay,
    LEFT(connection_reason, 50) as reason_preview
FROM thought_pairs
WHERE is_used_in_essay = FALSE
ORDER BY similarity_score DESC;
```

### 2. Step 4 실행
```bash
# 기본값 사용 (최대 5개 페어)
curl -X POST "http://localhost:8000/pipeline/generate-essays"

# 더 많은 페어 처리 (최대 10개)
curl -X POST "http://localhost:8000/pipeline/generate-essays?max_pairs=10"

# 에세이 목록 조회
curl "http://localhost:8000/pipeline/essays?limit=10"
```

### 3. 결과 검증
```sql
-- 생성된 에세이 확인
SELECT
    e.id,
    e.title,
    e.outline,
    e.reason,
    e.pair_id,
    e.generated_at,
    tp.similarity_score,
    tp.is_used_in_essay
FROM essays e
JOIN thought_pairs tp ON e.pair_id = tp.id
ORDER BY e.generated_at DESC;

-- 사용된 페어 확인
SELECT
    COUNT(*) FILTER (WHERE is_used_in_essay = TRUE) as used_pairs,
    COUNT(*) FILTER (WHERE is_used_in_essay = FALSE) as unused_pairs,
    COUNT(*) as total_pairs
FROM thought_pairs;
```

### 4. 전체 파이프라인 테스트
```bash
# Step 1-4 전체 실행
curl -X POST "http://localhost:8000/pipeline/run-all?max_essay_pairs=5"
```

---

## 성능 고려사항

### Claude API 호출 최적화
- 순차 처리 (배치 처리 불가, 각 페어마다 독립적 프롬프트)
- Rate limiting 준수 (5 req/sec)
- 예상 처리 시간: 5개 페어 = 약 10-15초
- 토큰 사용량: ~800-1200 tokens per request

### 부분 성공 전략
- 일부 페어 실패해도 성공한 것은 저장
- 에러 로깅 및 사용자에게 보고
- 실패한 페어는 is_used_in_essay = FALSE 유지 (재시도 가능)

### JSONB 필드 처리
- outline: list[str] → JSONB (자동 직렬화)
- used_thoughts_json: list[dict] → JSONB (model_dump() 사용)
- 조회 시 자동 파싱됨 (Supabase 클라이언트가 처리)

---

## 검증 스크립트 (임시 파일)

실행 후 다음 스크립트를 `temp/verification/`에 생성:

### `temp/verification/verify_step4.py`
```python
"""
Step 4 검증 스크립트

essays 테이블 데이터를 상세 분석하여 Step 4 완료 확인
"""

import asyncio
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add backend to path
backend_path = Path(__file__).parent.parent.parent / "backend"
sys.path.insert(0, str(backend_path))

# Load environment variables
env_path = backend_path / ".env"
load_dotenv(env_path)

from services.supabase_service import SupabaseService


async def main():
    """Step 4 검증"""
    print("=" * 70)
    print("Step 4 검증: essays 테이블 데이터 분석")
    print("=" * 70)

    supabase = SupabaseService()
    await supabase._ensure_initialized()

    try:
        # 1. essays 테이블 통계
        print("\n[1] essays 테이블 통계")
        print("-" * 70)

        response = await supabase.client.table("essays").select("*").execute()
        essays = response.data

        print(f"✓ 총 에세이 개수: {len(essays)}")

        if len(essays) == 0:
            print("\n⚠️  생성된 에세이가 없습니다. Step 4를 먼저 실행하세요.")
            return

        # 2. 상위 5개 에세이 상세 정보
        print("\n[2] 상위 5개 에세이 (최신순)")
        print("-" * 70)

        sorted_essays = sorted(essays, key=lambda x: x["generated_at"], reverse=True)

        for i, essay in enumerate(sorted_essays[:5], 1):
            print(f"\n{i}. Essay ID: {essay['id']} (Pair ID: {essay['pair_id']})")
            print(f"   제목: {essay['title']}")
            print(f"\n   [3단 개요]")
            for j, outline_item in enumerate(essay['outline'], 1):
                print(f"   {j}단: {outline_item[:100]}{'...' if len(outline_item) > 100 else ''}")
            print(f"\n   [선택 이유]")
            print(f"   {essay['reason'][:200]}{'...' if len(essay['reason']) > 200 else ''}")
            print(f"\n   [사용된 사고 단위: {len(essay['used_thoughts_json'])}개]")
            for thought in essay['used_thoughts_json']:
                print(f"   - Thought {thought['thought_id']} ({thought['source_title']})")
                print(f"     Claim: {thought['claim'][:80]}...")

        # 3. 데이터 무결성 검증
        print("\n[3] 데이터 무결성 검증")
        print("-" * 70)

        issues = []

        for essay in essays:
            # title 길이 검증
            if len(essay["title"]) < 5 or len(essay["title"]) > 100:
                issues.append(f"Essay {essay['id']}: title length ({len(essay['title'])}) out of range [5, 100]")

            # outline 개수 검증
            if len(essay["outline"]) != 3:
                issues.append(f"Essay {essay['id']}: outline count ({len(essay['outline'])}) != 3")

            # reason 길이 검증
            if len(essay["reason"]) > 300:
                issues.append(f"Essay {essay['id']}: reason too long (> 300 chars)")

            # used_thoughts 검증
            if len(essay["used_thoughts_json"]) < 1:
                issues.append(f"Essay {essay['id']}: no used_thoughts")

        if issues:
            print(f"✗ 발견된 문제: {len(issues)}개")
            for issue in issues[:5]:
                print(f"  - {issue}")
        else:
            print("✓ 데이터 무결성 검증 통과")

        # 4. thought_pairs 사용 상태 확인
        print("\n[4] thought_pairs 사용 상태")
        print("-" * 70)

        pairs_response = await supabase.client.table("thought_pairs").select("*").execute()
        all_pairs = pairs_response.data

        used_count = sum(1 for p in all_pairs if p["is_used_in_essay"])
        unused_count = len(all_pairs) - used_count

        print(f"✓ 총 페어: {len(all_pairs)}개")
        print(f"✓ 사용된 페어: {used_count}개")
        print(f"✓ 미사용 페어: {unused_count}개")

        # 5. 요약
        print("\n" + "=" * 70)
        print("검증 요약")
        print("=" * 70)
        print(f"✓ 총 에세이: {len(essays)}개")
        print(f"✓ 사용된 페어: {used_count}개")
        print(f"✓ 미사용 페어: {unused_count}개")
        print(f"✓ 무결성 이슈: {len(issues)}개")

        if len(issues) == 0:
            print("\n🎉 Step 4 검증 완료! 모든 에세이가 정상적으로 생성되었습니다.")
        else:
            print("\n⚠️  일부 문제가 발견되었습니다. 위 내용을 확인하세요.")

    finally:
        await supabase.close()


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 예상 결과

### Step 4 실행 성공 시:
```json
{
  "success": true,
  "pairs_processed": 5,
  "essays_generated": 5,
  "essays": [
    {
      "id": 1,
      "type": "essay",
      "title": "게임의 난이도 곡선과 교육의 최적 도전: 몰입을 설계하는 법",
      "outline": [
        "게임은 쉬운 것이 아니라 적절한 도전을 제공할 때 재미있다는 사실을 탐구한다.",
        "교육 현장에서도 학습자의 현재 수준보다 약간 높은 난이도를 제공할 때 최고의 몰입이 일어난다.",
        "게임 디자인과 교육 설계의 공통 원리를 통해 '최적의 도전'이 창의성과 성장의 핵심임을 제시한다."
      ],
      "reason": "서로 다른 도메인(게임, 교육)이지만 '최적 난이도'라는 공통 원리를 통해 몰입과 성장의 본질을 탐구할 수 있다. 독자는 게임과 학습의 의외의 연결점을 발견하게 된다.",
      "pair_id": 1,
      "used_thoughts": [
        {
          "thought_id": 8,
          "claim": "게임을 '쉬면서' 하는 것이 아니라, 플레이어의 현재 실력보다 약간 높은 난이도를 제공할 때 가장 재미있다.",
          "source_title": "게임은 쉬면서 하는게 아니라는 내 생각을 뒷받침하는 글",
          "source_url": "https://www.notion.so/..."
        },
        {
          "thought_id": 3,
          "claim": "학습에서 최적의 도전 수준은 현재 능력보다 약간 높은 지점이다.",
          "source_title": "교육심리학 - 몰입 이론",
          "source_url": "https://www.notion.so/..."
        }
      ],
      "generated_at": "2026-01-12T10:30:00Z"
    }
  ],
  "errors": []
}
```

### essays 테이블:
- 5개 행 생성
- title: 흥미로운 제목 (5-100자)
- outline: 정확히 3개 문장 (JSONB 배열)
- used_thoughts_json: 사용된 사고 단위 정보 (JSONB 객체 배열)
- reason: 선택 이유 (50-300자)
- pair_id: 외래키 (thought_pairs 참조)
- generated_at: 자동 타임스탬프

### thought_pairs 테이블:
- 사용된 5개 페어: is_used_in_essay = TRUE
- 미사용 5개 페어: is_used_in_essay = FALSE (다음 실행 대기)

---

## 주의사항 및 특이 케이스

### 1. Claude API 호출 관련
- **JSON 파싱 실패**: Step 2/3과 동일한 safe_json_parse() 사용
- **outline이 3개 아님**: Pydantic Field(..., min_length=3, max_length=3) 검증으로 자동 에러
- **title/reason 길이 초과**: Pydantic 검증으로 자동 에러
- **used_thoughts 누락**: Pydantic Field(..., min_length=1) 검증
- **프롬프트에서 명확한 지시**: "outline은 정확히 3개 문장", "reason은 한 줄"

### 2. DB 저장 관련
- **JSONB 직렬화**: list[str], list[dict] 자동 처리됨 (Supabase 클라이언트)
- **model_dump() 사용**: UsedThought 객체를 dict로 변환
- **외래키 제약**: pair_id가 존재하지 않으면 에러 (사전 검증됨)
- **CASCADE 삭제**: pair 삭제 시 essay도 자동 삭제

### 3. 부분 성공 처리
- **일부 페어 실패**: try-except로 개별 처리, 성공한 것만 저장
- **is_used_in_essay 업데이트 실패**: 무시 (에세이는 이미 저장됨)
- **에러 로깅**: 모든 실패 케이스 기록 및 사용자에게 보고

### 4. 페어 재사용 방지
- **is_used_in_essay = TRUE**: 자동으로 다음 실행에서 제외됨
- **수동 재사용**: 필요 시 is_used_in_essay를 FALSE로 변경 (SQL)
- **중복 에세이**: pair_id 외래키로 중복 방지는 안 됨 (의도적, 여러 번 생성 가능)

### 5. 타입 안전성
- **EssayCreate 모델**: Pydantic 검증으로 타입 보장
- **JSONB 필드**: Python list/dict ↔ PostgreSQL JSONB 자동 변환
- **datetime 직렬화**: generated_at은 ISO 8601 format

### 6. 에러 복구 전략
- **Claude 응답 파싱 실패**: raw content 로깅 + 다음 페어 계속 처리
- **Pydantic 검증 실패**: 검증 에러 상세 로깅 + 다음 페어 계속
- **DB 저장 실패**: 트랜잭션 롤백 + 전체 배치 실패 (재시도 가능)

### 7. 프롬프트 품질 보장
- **구체적 예시**: outline 각 단이 무엇을 담아야 하는지 명시
- **길이 제한**: 각 필드의 최소/최대 길이 명시
- **JSON 형식**: 정확한 JSON 구조 예시 제공
- **줄바꿈 금지**: reason은 한 줄로 작성 (JSONB 파싱 안정성)

---

## 🛡️ 에러 처리 전략 (상세)

### 기존 패턴 참고
Step 2/3의 검증된 에러 처리 패턴을 Step 4에도 동일하게 적용합니다.

#### 1. AI Service - generate_essay() 메서드

**패턴 1: Claude API 호출 에러**
```python
try:
    result = await self.generate_content_with_claude(...)

    if not result["success"]:
        raise Exception(
            f"Claude API error: {result.get('error', 'Unknown error')}"
        )
except Exception as e:
    logger.error(f"Failed to generate essay: {e}")
    raise  # 호출자(pipeline router)에게 전파
```

**패턴 2: JSON 파싱 에러 (safe_json_parse 사용)**
```python
from services.ai_service import safe_json_parse

# Claude 응답 파싱
raw_content = result["content"]
parsed_data = safe_json_parse(raw_content)

if parsed_data is None:
    logger.error(f"JSON parse failed. Raw content: {raw_content[:500]}")
    raise ValueError(f"Invalid JSON response from Claude")
```

**패턴 3: Pydantic 검증 에러**
```python
from pydantic import ValidationError

try:
    # EssayCreate 모델 검증 (자동으로 길이/타입 체크)
    essay = EssayCreate(**parsed_data)
except ValidationError as e:
    logger.error(f"Pydantic validation failed: {e}")
    logger.error(f"Raw data: {parsed_data}")
    raise ValueError(f"Essay validation failed: {e}")
```

#### 2. Supabase Service - Essay CRUD 메서드

**패턴 4: DB 저장 에러**
```python
async def insert_essay(self, essay: EssayCreate) -> dict:
    await self._ensure_initialized()

    try:
        # JSONB 직렬화
        essay_dict = {
            "type": essay.type,
            "title": essay.title,
            "outline": essay.outline,  # list → JSONB (자동)
            "used_thoughts_json": [t.model_dump() for t in essay.used_thoughts],
            "reason": essay.reason,
            "pair_id": essay.pair_id
        }

        response = await self.client.table("essays")\
            .insert(essay_dict)\
            .execute()

        inserted = response.data[0]
        logger.info(f"Inserted essay ID {inserted['id']} for pair {essay.pair_id}")
        return inserted

    except Exception as e:
        logger.error(f"Failed to insert essay: {e}")
        logger.error(f"Essay data: {essay_dict}")
        raise  # 호출자에게 전파
```

**패턴 5: 외래키 제약 위반 (pair_id 존재 확인)**
```python
# Router에서 사전 검증 (선택 사항)
try:
    pair_data = await supabase_service.get_pair_with_thoughts(pair_id)
except Exception as e:
    logger.error(f"Pair {pair_id} not found: {e}")
    raise HTTPException(
        status_code=404,
        detail=f"Pair {pair_id} does not exist"
    )
```

#### 3. Pipeline Router - generate_essays 엔드포인트

**패턴 6: 부분 성공 허용 (개별 try-except)**
```python
generated_essays: List[EssayCreate] = []
processed_pair_ids: List[int] = []

for pair in unused_pairs:
    pair_id = pair["id"]
    try:
        result["pairs_processed"] += 1

        # 페어 데이터 가져오기
        pair_data = await supabase_service.get_pair_with_thoughts(pair_id)

        # Claude로 에세이 생성
        essay_dict = await ai_service.generate_essay(pair_data)

        # Pydantic 모델 생성
        essay = EssayCreate(
            title=essay_dict["title"],
            outline=essay_dict["outline"],
            used_thoughts=essay_dict["used_thoughts"],
            reason=essay_dict["reason"],
            pair_id=pair_id
        )

        generated_essays.append(essay)
        processed_pair_ids.append(pair_id)
        logger.info(f"✓ Essay generated for pair {pair_id}")

    except Exception as e:
        # 개별 실패는 로깅만 하고 계속 진행
        error_msg = f"Failed to generate essay for pair {pair_id}: {str(e)}"
        logger.error(error_msg, exc_info=True)  # 스택 트레이스 포함
        result["errors"].append(error_msg)
        # 계속 진행 (다른 페어는 성공할 수 있음)
```

**패턴 7: 미사용 페어 없음 에러**
```python
unused_pairs = await supabase_service.get_unused_thought_pairs(limit=max_pairs)

if not unused_pairs:
    logger.warning("No unused pairs found")
    result["errors"].append("No unused pairs available. Run Step 3 first.")
    return result  # 에러 코드 없이 빈 결과 반환
```

**패턴 8: 배치 저장 실패 (전체 롤백)**
```python
if generated_essays:
    try:
        logger.info(f"Saving {len(generated_essays)} essays to DB...")
        saved_essays = await supabase_service.insert_essays_batch(generated_essays)
        result["essays_generated"] = len(saved_essays)
        result["essays"] = saved_essays
        result["success"] = True

    except Exception as e:
        # 배치 저장 실패 시 전체 롤백
        logger.error(f"Batch insert failed: {e}", exc_info=True)
        result["errors"].append(f"Failed to save essays: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Essay batch insert failed: {str(e)}"
        )
```

**패턴 9: is_used_in_essay 업데이트 실패 (무시)**
```python
# 에세이는 이미 저장됨, 업데이트 실패는 치명적이지 않음
for pair_id in processed_pair_ids:
    try:
        await supabase_service.update_pair_used_status(pair_id, is_used=True)
    except Exception as e:
        # 로깅만 하고 무시 (에세이는 이미 저장됨)
        logger.error(f"Failed to update pair {pair_id} status: {e}")
        # HTTPException 발생하지 않음
```

#### 4. 에러 메시지 가이드

**사용자 친화적인 에러 메시지 작성:**

```python
# ❌ 나쁜 예
raise HTTPException(status_code=500, detail="Error")

# ✅ 좋은 예
raise HTTPException(
    status_code=404,
    detail=(
        f"No unused pairs available. "
        f"Please run Step 3 first to generate thought pairs. "
        f"Current status: {len(all_pairs)} total pairs, "
        f"{used_count} already used."
    )
)
```

**로깅 레벨 구분:**
- `logger.info()`: 정상 진행 상황 (페어 조회 성공, 에세이 생성 성공)
- `logger.warning()`: 비정상이지만 복구 가능 (후보 0개 → fallback)
- `logger.error()`: 에러 발생, 재시도 필요 (Claude API 실패, DB 저장 실패)
- `exc_info=True`: 스택 트레이스 포함 (디버깅 필수)

#### 5. 재시도 로직 (선택적)

**generate_essay()에서 재시도 (safe_json_parse와 유사):**
```python
max_retries = 2
last_error = None

for attempt in range(max_retries + 1):
    try:
        result = await self.generate_content_with_claude(...)

        if not result["success"]:
            raise Exception(f"Claude API error: {result.get('error')}")

        raw_content = result["content"]
        parsed_data = safe_json_parse(raw_content)

        if parsed_data is None:
            raise ValueError("JSON parse failed")

        # Pydantic 검증
        essay_data = EssayCreate.model_validate(parsed_data)

        logger.info(f"Essay generated successfully (attempt {attempt + 1})")
        return essay_data.model_dump()

    except Exception as e:
        last_error = e
        logger.warning(f"Attempt {attempt + 1}/{max_retries + 1} failed: {e}")

        if attempt < max_retries:
            logger.info("Retrying...")
            continue
        else:
            logger.error(f"All {max_retries + 1} attempts failed")
            raise last_error
```

#### 6. 테스트용 에러 시나리오

구현 후 다음 시나리오를 수동으로 테스트:

1. **미사용 페어 0개**: Step 3 실행 전 Step 4 호출 → 명확한 에러 메시지
2. **Claude API 실패**: API 키 잘못 입력 → 적절한 에러 메시지
3. **JSON 파싱 실패**: safe_json_parse() 동작 확인
4. **Pydantic 검증 실패**: outline 4개인 응답 → 검증 에러
5. **DB 저장 실패**: 존재하지 않는 pair_id 사용 → 외래키 에러
6. **부분 성공**: 5개 중 2개 실패 → 3개는 저장, 2개는 에러 로깅

---

## 🔧 구현 시 체크리스트

### Phase 1 완료 조건
- [ ] ai_service.py의 generate_essay() 메서드 추가
- [ ] 프롬프트 설계 (system_message + prompt)
- [ ] safe_json_parse() 사용
- [ ] EssayCreate 모델로 Pydantic 검증
- [ ] used_thoughts 리스트 생성 로직

### Phase 2 완료 조건
- [ ] supabase_service.py에 4개 메서드 추가
  - [ ] insert_essay()
  - [ ] insert_essays_batch()
  - [ ] get_essays()
  - [ ] get_essay_by_id()
- [ ] JSONB 직렬화 (model_dump() 사용)
- [ ] 에러 처리 (try-except + 로깅)

### Phase 3 완료 조건
- [ ] /pipeline/generate-essays 엔드포인트
- [ ] 부분 성공 로직 (일부 실패해도 계속)
- [ ] is_used_in_essay 업데이트
- [ ] /pipeline/essays 엔드포인트
- [ ] /pipeline/run-all 확장 (Step 4 추가)

### 테스트 완료 조건
- [ ] 미사용 페어 확인 쿼리 실행
- [ ] Step 4 실행 (curl 또는 Swagger UI)
- [ ] essays 테이블 데이터 검증
- [ ] thought_pairs 사용 상태 확인
- [ ] 전체 파이프라인 테스트

### 문서화 완료 조건
- [ ] temp/verification/verify_step4.py 생성
- [ ] 실행 결과 확인 및 문서화
- [ ] README 업데이트 (Step 4 섹션 추가)

---

## ⚠️ 치명적 에러 방지 규칙

1. **schemas/essay.py 변경 금지**: 이미 완성되어 있음, 그대로 사용
2. **JSONB 직렬화 필수**: list[str], list[dict] 직접 저장 가능 (Supabase 자동 처리)
3. **model_dump() 사용**: UsedThought 객체를 dict로 변환 필요
4. **부분 성공 허용**: 일부 페어 실패해도 성공한 것은 저장 (try-except 개별 처리)
5. **is_used_in_essay 업데이트**: essay 저장 후 반드시 업데이트 (재사용 방지)

---

## 🎯 Step 4 완료 기준

다음 조건을 모두 만족하면 Step 4 완료:

1. ✅ ai_service.generate_essay() 메서드 구현 완료
2. ✅ supabase_service.py에 4개 essay CRUD 메서드 추가 완료
3. ✅ /pipeline/generate-essays 엔드포인트 구현 완료
4. ✅ /pipeline/essays 엔드포인트 구현 완료
5. ✅ /pipeline/run-all 확장 (Step 4 포함) 완료
6. ✅ 5개 에세이 생성 성공 (curl 테스트)
7. ✅ essays 테이블에 데이터 정상 저장 (SQL 검증)
8. ✅ thought_pairs.is_used_in_essay = TRUE 업데이트 확인
9. ✅ verify_step4.py 검증 스크립트 실행 성공
10. ✅ 무결성 이슈 0개 (title, outline, reason 길이 등)

---

## 📌 다음 단계 (Step 4 이후)

### MVP 완료 체크리스트
- [x] Step 1: Notion → raw_notes
- [x] Step 2: raw_notes → thought_units (임베딩)
- [x] Step 3: thought_units → thought_pairs (낮은 유사도 + 동일 출처 제외 + threshold)
- [ ] Step 4: thought_pairs → essays (글감 생성)
- [ ] Frontend: Next.js 대시보드 (에세이 목록 표시)
- [ ] 배포: Vercel (Frontend) + Supabase (Backend)

### MVP+1 개선 (우선순위)
1. **다중 Pairs 지원**: 2-5개 페어를 조합하여 더 풍부한 글감 생성
2. **자동 스케줄링**: 매일 자동으로 새 글감 생성
3. **사용자 피드백**: 에세이 평가 및 개선 루프
4. **Notion 연동 강화**: 생성된 글감을 Notion에 자동 저장

---

**Step 4 구현 플랜 완료! 이제 구현 시작 가능합니다.**
