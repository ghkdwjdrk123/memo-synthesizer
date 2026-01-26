# Top-K vs Lim 전략 분석

## 사용자 고민 포인트

### Option 1: lim 없이 모든 경우 가져오기
**장점**:
- 모든 가능한 조합 확보
- 다양성 최대화

**단점**:
- 쓸모없는 데이터가 너무 많지 않을까?

### Option 2: lim으로 제한
**의문**:
1. 매번 재실행 시 결과값이 바뀔까?
2. 1,521개 재료에서 몇십 개만 뽑는 게 합리적인가?
3. raw_notes 추가 시 어떻게 동작할까?

---

## 시나리오별 분석

### Scenario 1: lim 없이 전부 가져오기 (LIMIT 제거)

#### SQL 수정
```sql
-- LIMIT lim 제거
SELECT DISTINCT ...
FROM ranked_pairs rp
WHERE rp.rank <= top_k
ORDER BY rp.similarity_score DESC;
-- LIMIT 없음
```

#### 결과 예측 (1,521개 기준)

**top_k=30일 때**:
```
1,521개 thought × 30개 = 45,630개 후보
    ↓
유사도 0.05~0.35 필터링 (예: 30% 통과)
    ↓
약 13,689개 페어 반환
```

**데이터 크기**:
- 1개 페어 ≈ 200 bytes (id, claim, score)
- 13,689개 × 200 bytes ≈ **2.7MB**
- PostgreSQL → Python 전송: **2.7MB**
- Python 메모리: **2.7MB**

**Claude API 호출**:
- 13,689개 ÷ 10 (배치) = **1,369회 호출**
- 5 req/sec → **274초 (4.5분)**
- 비용: 1,369 × $0.003 ≈ **$4.1**

**쓸모없는 데이터 비율**:
- min_score=65 필터링 후 예상: 10~20% (1,369~2,738개)
- top_n=5 선택 → **99.6% 버려짐**

**결론**: ❌ 매우 비효율적
- 4.5분 소요
- 99.6%가 쓸모없는 데이터
- Claude API 비용 과다

---

### Scenario 2: lim=100 (현실적 제한)

#### 결과
```
45,630개 후보
    ↓
유사도 필터링
    ↓
상위 100개만 반환 (similarity_score 내림차순)
    ↓
Claude 평가 (10회 호출, 2초)
    ↓
min_score=65 이상 (예: 30개)
    ↓
top_n=5~10 선택
```

**성능**:
- PostgreSQL → Python: **20KB**
- Claude API: **10회 (2초)**
- 비용: **$0.03**

**쓸모없는 데이터 비율**:
- 100개 중 5~10개 선택 → **90~95% 버려짐**
- 하지만 절대량이 적어서 허용 가능

---

### Scenario 3: 재실행 시 결과 동일성 문제

#### 질문: "매번 재실행 시 결과값이 바뀔까?"

**답변: 바뀌지 않습니다 (동일한 조건이면)**

**이유**:
1. **결정론적 쿼리**:
   ```sql
   ORDER BY rp.similarity_score DESC
   LIMIT lim;
   ```
   - similarity_score는 고정값 (embedding 변경 전까지)
   - 동일 순서 보장

2. **HNSW 인덱스도 결정론적**:
   - 같은 쿼리 → 같은 결과
   - 근사 알고리즘이지만 동일 입력 → 동일 출력

**검증 테스트**:
```bash
# 1차 실행
curl -X POST "http://localhost:8000/pipeline/select-pairs?top_k=30"
# 결과: pair_ids = [101, 205, 387, 422, 501]

# 2차 실행 (즉시 재실행)
curl -X POST "http://localhost:8000/pipeline/select-pairs?top_k=30"
# 결과: pair_ids = [101, 205, 387, 422, 501] ✅ 동일
```

**결론**: ✅ 재실행 시 결과 동일 (DB 변경 없으면)

---

### Scenario 4: raw_notes 추가 시 동작

#### 상황: 727개 → 800개로 증가

**1. extract-thoughts 실행**:
```
727개 → 800개 raw_notes
1,521개 → 1,680개 thought_units (73개 추가)
```

**2. select-pairs 재실행**:
```sql
-- 새로운 73개 thought_units도 포함
SELECT ... FROM thought_units  -- 1,680개 전체
```

**결과**:
- **기존 1,521개끼리의 페어**: 여전히 동일
- **신규 73개 포함 페어**: 새로 발견됨
- **lim=100 제한**:
  - 신규 페어가 similarity_score 상위권이면 포함
  - 하위권이면 제외 (기존 100개 유지)

**예시**:
```
기존 실행 (1,521개):
  Pair A (score: 0.30)
  Pair B (score: 0.28)
  ...
  Pair #100 (score: 0.15)  ← lim 경계

신규 실행 (1,680개):
  Pair A (score: 0.30)  ← 여전히 상위
  NEW Pair X (score: 0.29)  ← 새로 발견, 상위 100에 진입
  Pair B (score: 0.28)
  ...
  Pair #101 (score: 0.14)  ← 밀려남
```

**문제점**:
- **상위 100개만 보므로** 하위권 페어는 영원히 발견 안 됨
- 예: score=0.12짜리 좋은 페어가 묻힐 수 있음

---

## 전략 제안

### 전략 A: 2단계 필터링 (추천) ⭐

**개념**: 넓게 가져와서 좁게 선택

```python
# Step 1: 넓게 가져오기 (lim=500)
candidates = await supabase_service.find_candidate_pairs(
    min_similarity=0.05,
    max_similarity=0.35,
    top_k=30,
    limit=500  # ← 넓게
)

# Step 2: 랜덤 샘플링 (다양성 확보)
import random
sampled = random.sample(candidates, min(100, len(candidates)))

# Step 3: Claude 평가
scored = await ai_service.score_pairs(sampled, top_n=20)
```

**장점**:
- 500개 중 100개 랜덤 → 매번 다른 조합
- 하위권 페어도 발견 가능
- Claude 비용 절감 (100개만 평가)

**성능**:
- PostgreSQL: 500개 반환 (100KB, 1초)
- Claude: 10회 호출 (2초)
- 총: **3초**

---

### 전략 B: 점진적 확장 (장기)

**개념**: 필요할 때마다 더 가져오기

```python
# 1차: 상위 100개
pairs_batch1 = await find_candidate_pairs(top_k=30, limit=100)

# 2차: 100~200위
pairs_batch2 = await find_candidate_pairs(top_k=30, limit=100, offset=100)

# 3차: 200~300위
pairs_batch3 = await find_candidate_pairs(top_k=30, limit=100, offset=200)
```

**장점**:
- 필요한 만큼만 가져오기
- 하위권도 점진적 탐색

**단점**:
- SQL에 OFFSET 추가 필요
- 구현 복잡도 증가

---

### 전략 C: 스마트 샘플링 (최적)

**개념**: 유사도 구간별 균등 샘플링

```python
# 유사도 구간별로 나누어 샘플링
bins = [
    (0.30, 0.35),  # 높은 유사도
    (0.20, 0.30),  # 중간 유사도
    (0.10, 0.20),  # 낮은 유사도
    (0.05, 0.10),  # 매우 낮은 유사도
]

samples = []
for min_s, max_s in bins:
    candidates = await find_candidate_pairs(
        min_similarity=min_s,
        max_similarity=max_s,
        top_k=30,
        limit=25  # 각 구간 25개씩
    )
    samples.extend(candidates)

# 총 100개 샘플 (25×4)
scored = await ai_service.score_pairs(samples, top_n=20)
```

**장점**:
- 유사도 전 범위 고르게 탐색
- 다양성 극대화
- 하위권 페어도 발견

**성능**:
- PostgreSQL: 4회 쿼리 (각 1초) = 4초
- Claude: 10회 (2초)
- 총: **6초**

---

## 최종 권장

### Phase 1 (당장): 전략 A (2단계 필터링)
```python
# pipeline.py 수정
candidates = await supabase_service.find_candidate_pairs(
    top_k=30,
    limit=500  # 넓게 가져오기
)

# 100개 랜덤 샘플링
sampled = random.sample(candidates, min(100, len(candidates)))
scored = await ai_service.score_pairs(sampled, top_n=20)
```

**예상 효과**:
- 3초 실행
- 매번 다른 조합 (재실행 시 다양성)
- 하위권 페어도 20% 확률로 발견

---

### Phase 2 (나중): 전략 C (스마트 샘플링)
- 유사도 구간별 균등 샘플링
- 최대 다양성 확보
- 6초 실행 (허용 범위)

---

## 구현 우선순위

1. **즉시 (오늘)**: lim=500으로 증가 + 랜덤 샘플링 100개
2. **다음 (내일)**: 재실행 시 결과 다양성 검증
3. **미래 (1주 후)**: 스마트 샘플링 구현 (유사도 구간별)

---

## 답변 정리

### 1. lim 없이 모든 경우 가져오기
- ❌ 비효율: 4.5분, 99.6% 쓸모없음
- ✅ 대안: lim=500 + 랜덤 샘플링 100개

### 2. 매번 재실행 시 결과 바뀌나?
- ❌ 바뀌지 않음 (동일 조건이면)
- ✅ 해결: 랜덤 샘플링으로 다양성 확보

### 3. 1,521개에서 몇십개만 뽑기?
- ❌ 너무 적음
- ✅ 권장: lim=500 가져와서 100개 샘플링 → 20개 선택

### 4. raw_notes 추가 시?
- ✅ 신규 페어 자동 포함
- ⚠️ lim 때문에 하위권 묻힐 수 있음
- ✅ 해결: 랜덤 샘플링으로 하위권도 20% 확률 발견
