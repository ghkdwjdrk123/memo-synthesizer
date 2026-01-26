# 추천 알고리즘 설계 문서

## 개요

`RecommendationEngine`은 quality tier와 다양성을 고려하여 Essay 작성에 적합한 thought pair를 추천합니다.

## 핵심 알고리즘

### 1. Quality Tier 기반 조회

```python
quality_tiers = ["excellent", "premium", "standard"]  # 우선순위 순서
```

- **Excellent (95-100점)**: 논리적 확장 가능성이 매우 높음
- **Premium (85-94점)**: 논리적 확장 가능성이 높음
- **Standard (65-84점)**: 논리적 확장 가능성이 보통

tier별로 순차 조회하여 우선순위를 보장합니다.

### 2. 다양성 스코어 계산

```python
diversity_score = 1 / (count_a + count_b)
```

- `count_a`: thought_a의 raw_note_id가 전체 후보에서 등장한 횟수
- `count_b`: thought_b의 raw_note_id가 전체 후보에서 등장한 횟수

**의미**:
- 자주 사용된 메모일수록 다양성 점수가 낮음
- 새로운 메모 조합을 선호

**예시**:
- Note A가 10번, Note B가 5번 등장 → diversity_score = 1 / (10 + 5) = 0.067
- Note C가 2번, Note D가 1번 등장 → diversity_score = 1 / (2 + 1) = 0.333

### 3. 최종 점수 계산

```python
final_score = claude_score × (1 - w) + (diversity_score × 100) × w
```

- `claude_score`: Claude LLM 평가 점수 (0-100)
- `diversity_score`: 다양성 점수 (0-1)
- `w`: diversity_weight (기본 0.3)

**diversity_score × 100**: diversity_score를 0-100 스케일로 맞춤

**가중치 의미**:
- `w = 0`: 100% Claude 점수 (품질 우선)
- `w = 0.3`: 70% Claude 점수 + 30% 다양성 (기본 균형)
- `w = 0.5`: 50% Claude 점수 + 50% 다양성 (균형)
- `w = 1`: 100% 다양성 (새로운 조합 우선)

## 사용 사례

### Case 1: 고품질 Essay (Excellent only)

```python
pairs = await engine.get_recommended_pairs(
    limit=5,
    quality_tiers=["excellent"],
    diversity_weight=0.3  # 품질 우선, 다양성은 보조
)
```

**시나리오**: 중요한 발표 자료, 블로그 핵심 글

### Case 2: 다양한 주제 탐색 (High diversity)

```python
pairs = await engine.get_recommended_pairs(
    limit=20,
    quality_tiers=["excellent", "premium", "standard"],
    diversity_weight=0.7  # 다양성 우선
)
```

**시나리오**: 브레인스토밍, 새로운 아이디어 발굴

### Case 3: 균형 잡힌 추천 (Default)

```python
pairs = await engine.get_recommended_pairs(limit=10)
```

**시나리오**: 일반적인 Essay 작성

## 성능 특성

### 쿼리 최적화

```sql
-- tier별 조회 (인덱스 활용)
SELECT ... FROM thought_pairs
WHERE is_used_in_essay = FALSE
  AND quality_tier = 'excellent'
  AND claude_score IS NOT NULL
ORDER BY claude_score DESC
LIMIT 20;

-- 사용된 인덱스:
-- idx_thought_pairs_quality_unused (quality_tier, claude_score DESC)
```

### 시간 복잡도

- **DB 조회**: O(tier 개수 × log N) (인덱스 활용)
- **다양성 계산**: O(M) (M = 조회된 페어 개수, 보통 limit × 2)
- **정렬**: O(M log M)

**총 복잡도**: O(M log M) (M ≈ 20-60)

### 메모리 사용

- 최대 `limit × 2 × tier 개수`개의 페어를 메모리에 로드
- 기본값: 10 × 2 × 3 = 60개 페어 (각 페어 ~1KB → 60KB)

## JOIN 구조

```sql
-- thought_units와 2번 JOIN (raw_note_id 필요)
SELECT
    tp.id,
    tp.claude_score,
    tp.quality_tier,
    ta.raw_note_id as note_id_a,
    tb.raw_note_id as note_id_b
FROM thought_pairs tp
JOIN thought_units ta ON tp.thought_a_id = ta.id
JOIN thought_units tb ON tp.thought_b_id = tb.id
WHERE tp.is_used_in_essay = FALSE
```

Supabase는 이를 자동으로 처리:

```python
.select("""
    id, claude_score, quality_tier,
    thought_a:thought_units!thought_pairs_thought_a_id_fkey(raw_note_id),
    thought_b:thought_units!thought_pairs_thought_b_id_fkey(raw_note_id)
""")
```

## 확장 가능성

### 추가 가능한 가중치

```python
async def get_recommended_pairs_advanced(
    self,
    limit: int = 10,
    quality_tiers: List[str] = None,
    diversity_weight: float = 0.3,
    recency_weight: float = 0.0,  # 신규 페어 우선
    user_preference_weight: float = 0.0  # 사용자 선호도
) -> List[Dict]:
    # ...
    final_score = (
        claude_score * quality_weight +
        (diversity_score * 100) * diversity_weight +
        (recency_score * 100) * recency_weight +
        (preference_score * 100) * user_preference_weight
    )
```

### 필터링 옵션

```python
async def get_recommended_pairs_filtered(
    self,
    limit: int = 10,
    min_claude_score: int = 65,  # 최소 점수
    exclude_note_ids: List[str] = None,  # 제외할 메모
    require_different_notes: bool = True  # 서로 다른 메모만
) -> List[Dict]:
    # ...
```

## 테스트 시나리오

### 1. 빈 결과 처리

```python
# DB에 미사용 페어가 없는 경우
pairs = await engine.get_recommended_pairs(limit=10)
assert pairs == []
```

### 2. 다양성 극단값

```python
# w=0: Claude 점수만 고려
pairs_quality = await engine.get_recommended_pairs(diversity_weight=0.0)

# w=1: 다양성만 고려
pairs_diverse = await engine.get_recommended_pairs(diversity_weight=1.0)

# 순서가 달라야 함
assert pairs_quality != pairs_diverse
```

### 3. Tier 우선순위

```python
# Excellent만 조회
pairs_excellent = await engine.get_recommended_pairs(
    quality_tiers=["excellent"]
)

# 모든 pair의 quality_tier가 'excellent'여야 함
assert all(p["quality_tier"] == "excellent" for p in pairs_excellent)
```

## 로깅

### INFO 레벨

```
INFO: Getting recommended pairs: limit=10, tiers=['excellent', 'premium'], diversity_weight=0.30
INFO: Retrieved 15 pairs from tier 'excellent'
INFO: Retrieved 10 pairs from tier 'premium'
INFO: Total pairs retrieved: 25
INFO: Returning 10 recommended pairs (top score: 94.50, bottom score: 87.20)
```

### DEBUG 레벨

```
DEBUG: Note usage counts: 12 unique notes
DEBUG: Pair 123: claude=95, diversity=0.0667, final=88.00
DEBUG: Pair 456: claude=90, diversity=0.1333, final=91.00
```

### WARNING 레벨

```
WARNING: Pair 789 missing thought_a or thought_b, skipping
WARNING: Invalid diversity_weight 1.5, clamping to [0, 1]
```

## 배포 체크리스트

- [ ] Migration 007 실행 (claude_score, quality_tier 컬럼 추가)
- [ ] 인덱스 생성 확인 (idx_thought_pairs_quality_unused)
- [ ] 기존 페어에 claude_score 채우기 (배치 워커 실행)
- [ ] API 엔드포인트 추가 (/recommendations)
- [ ] 프론트엔드 통합 (추천 목록 UI)
- [ ] 테스트 작성 및 실행
- [ ] 로깅 모니터링 설정

## 참고 자료

- [thought_pairs 테이블 스키마](../docs/supabase_migrations/007_extend_thought_pairs.sql)
- [SupabaseService 메서드](../services/supabase_service.py)
- [사용 예시 코드](../docs/recommendation_usage_example.py)
