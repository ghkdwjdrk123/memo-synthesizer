# Distance Table 페어 누락 문제 분석 및 해결

## 🔴 문제 요약

**증상:**
- 예상 페어: **1,821,186개** (1,909 thoughts)
- 실제 페어: **455,124개** (25%)
- **75% 누락** (약 1,366,062개)

**영향:**
- thought_a_id 범위: 251 ~ 1908 (**ID 1-250 완전 누락**)
- 작은 ID끼리의 페어 전체 누락
- p10_p40 범위에서 2,187개만 수집 (예상 수만 개)

---

## 🔍 원인 분석

### 문제가 있는 SQL (011_build_distance_table_rpc.sql)

```sql
SELECT
    a.id AS thought_a_id,
    b.id AS thought_b_id,
    (1 - (a.embedding <=> b.embedding))::FLOAT AS similarity
FROM (
    SELECT id, embedding, raw_note_id
    FROM thought_units
    WHERE embedding IS NOT NULL
    ORDER BY id
    LIMIT 50 OFFSET 0  -- 배치 1: ID 1-50
) a
CROSS JOIN thought_units b
WHERE b.id > a.id  -- ⚠️ 문제의 조건!
  AND b.embedding IS NOT NULL
  AND b.raw_note_id != a.raw_note_id
```

### 배치별 실행 과정

| 배치 | a (배치 내) | b (전체) | 생성되는 페어 | 누락되는 페어 |
|------|------------|----------|-------------|-------------|
| 1 | ID 1-50 | b.id > a.id | (1, 51-1909), (2, 51-1909), ... | **❌ (1,2), (1,3), ..., (49,50)** |
| 2 | ID 51-100 | b.id > a.id | (51, 101-1909), (52, 101-1909), ... | **❌ (51,1-50), (52,1-50), ...** |
| 3 | ID 101-150 | b.id > a.id | (101, 151-1909), ... | **❌ (101,1-100), ...** |
| ... | ... | ... | ... | ... |
| 39 | ID 1901-1909 | b.id > a.id | 거의 없음 | **❌ (1901,1-1900), ...** |

**결과:**
- **초반 배치는 많은 페어 생성** (배치 1: ~95,000개)
- **후반 배치는 거의 생성 안됨** (배치 39: ~10개)
- **작은 ID끼리의 모든 페어 누락**

### 수학적 분석

```
WHERE b.id > a.id 조건 하에서:
- 배치 1 (ID 1-50):   각 thought가 1859개 페어 생성 (평균)
- 배치 39 (ID 1851-1900): 각 thought가 9개 페어 생성 (평균)

→ 초반에 집중, 후반에 거의 없음
→ 총 455,124개 (예상 1,821,186개의 25%)
```

---

## ✅ 해결 방안

### 수정된 SQL (011_build_distance_table_rpc_v2.sql)

```sql
SELECT
    LEAST(a.id, b.id) AS thought_a_id,      -- 항상 작은 ID
    GREATEST(a.id, b.id) AS thought_b_id,   -- 항상 큰 ID
    (1 - (a.embedding <=> b.embedding))::FLOAT AS similarity
FROM (
    SELECT id, embedding, raw_note_id
    FROM thought_units
    WHERE embedding IS NOT NULL
    ORDER BY id
    LIMIT 50 OFFSET 0
) a
CROSS JOIN thought_units b
WHERE a.id != b.id                    -- ✅ 자기 자신만 제외
  AND b.embedding IS NOT NULL
  AND a.raw_note_id != b.raw_note_id
ON CONFLICT (thought_a_id, thought_b_id) DO NOTHING  -- UNIQUE 제약으로 중복 자동 제거
```

### 주요 변경사항

| 항목 | 이전 (v1) | 수정 (v2) |
|------|----------|-----------|
| **조건** | `WHERE b.id > a.id` | `WHERE a.id != b.id` |
| **정렬** | `a.id, b.id` (그대로) | `LEAST(a.id, b.id), GREATEST(a.id, b.id)` |
| **중복 제거** | 조건으로 회피 | UNIQUE 제약 + ON CONFLICT |

### 작동 방식

1. **배치 1 (ID 1-50)**:
   - a = 1-50, b = 1-1909 (자기 자신 제외)
   - 페어: (1,2), (1,3), ..., (1,1909), (2,1), (2,3), ...
   - LEAST/GREATEST: (1,2), (1,3), ..., (1,1909), (1,2) (중복), ...
   - ON CONFLICT: 중복 자동 제거
   - 결과: ~95,450개 페어 생성

2. **배치 2 (ID 51-100)**:
   - a = 51-100, b = 1-1909 (자기 자신 제외)
   - 페어: (51,1), (51,2), ..., (51,1909), (52,1), ...
   - LEAST/GREATEST: (1,51), (2,51), ..., (51,1909), (1,52), ...
   - ON CONFLICT: (1,51), (2,51) 등은 배치 1에서 이미 생성 → 중복 제거
   - 결과: ~90,000개 신규 페어 생성

3. **배치 39 (ID 1901-1909)**:
   - a = 1901-1909, b = 1-1909
   - 대부분 이전 배치에서 이미 생성됨
   - ON CONFLICT로 중복 제거
   - 결과: ~3,600개 신규 페어 생성

---

## 📊 예상 결과

### 수정 전 (v1)

| 항목 | 값 |
|------|-----|
| 총 페어 | 455,124개 (25%) |
| thought_a_id 범위 | 251 ~ 1908 |
| 누락 | 1,366,062개 (75%) |

### 수정 후 (v2)

| 항목 | 값 |
|------|-----|
| 총 페어 | **1,821,186개 (100%)** |
| thought_a_id 범위 | **1 ~ 1908** |
| 누락 | **0개** |

### 성능 예상

```
1,909 thoughts × batch_size 50:
- 배치 수: 39회
- 각 배치 시간: ~12-15초 (v1: 10초 → v2: 약간 증가)
- 총 시간: 39 × 13초 = ~8-9분 (v1: 38.7초 → v2: 8분, 중복 처리로 증가)
```

**Trade-off:**
- v1: 38.7초, 25% 데이터만
- v2: 8-9분, 100% 데이터 ✅

---

## 🚀 실행 방법

### 1. Supabase SQL Editor에서 실행

```sql
-- 1. 기존 Distance Table 초기화
TRUNCATE TABLE thought_pair_distances RESTART IDENTITY;

-- 2. 수정된 RPC 함수 배포
-- 파일: backend/docs/supabase_migrations/011_build_distance_table_rpc_v2.sql
-- Supabase SQL Editor에서 전체 내용 복사 → 실행
```

### 2. Python API로 재구축

```bash
# FastAPI 서버 실행 (이미 실행 중이면 스킵)
cd backend
uvicorn main:app --reload

# 새 터미널에서 재구축 실행
curl -X POST "http://localhost:8000/pipeline/distance-table/build?batch_size=50"
```

**예상 시간:** 8-9분 (1,909 thoughts 기준)

### 3. 검증

```bash
# 상태 조회
curl -X GET "http://localhost:8000/pipeline/distance-table/status"

# 예상 결과:
# {
#   "success": true,
#   "statistics": {
#     "total_pairs": 1821186,  # ✅ 1,821,186개
#     "min_similarity": 0.001,
#     "max_similarity": 0.987,
#     "avg_similarity": 0.342
#   }
# }
```

---

## 📋 체크리스트

- [ ] Supabase에서 기존 Distance Table TRUNCATE
- [ ] 011_build_distance_table_rpc_v2.sql 실행
- [ ] Python API로 재구축 (batch_size=50)
- [ ] 총 페어 개수 확인 (예상: 1,821,186개)
- [ ] thought_a_id 범위 확인 (예상: 1 ~ 1908)
- [ ] 샘플 페어 확인 (1,2), (1,3), (50,51) 등 존재 여부
- [ ] collect-candidates 재테스트 (p10_p40 범위)

---

## 🎯 예상 개선 효과

| 항목 | 수정 전 | 수정 후 | 개선율 |
|------|---------|---------|--------|
| 총 페어 | 455,124 | **1,821,186** | **400%** |
| p10_p40 수집 | 2,187개 | **~54,000개 예상** | **2,500%** |
| 데이터 정합성 | 25% | **100%** | **300%** |
| ID 범위 | 251-1908 | **1-1908** | 완전 복구 |

---

## 📝 학습 포인트

**배치 처리에서 주의사항:**

1. **조건부 필터링의 위험성:**
   - `WHERE b.id > a.id`는 단일 쿼리에서는 완벽
   - 배치 처리에서는 이전 배치와의 관계를 고려 못함

2. **올바른 접근:**
   - 모든 페어를 생성 (a.id != b.id)
   - DB 제약조건으로 중복 제거 (UNIQUE + ON CONFLICT)
   - LEAST/GREATEST로 정렬 보장

3. **검증의 중요성:**
   - 예상 개수 = n(n-1)/2
   - ID 범위 확인 (min, max)
   - 샘플 페어 존재 여부 체크

---

**다음 단계:** 수정된 SQL 배포 후 Distance Table 재구축
