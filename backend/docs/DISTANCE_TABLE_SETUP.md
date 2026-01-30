# Distance Table 구축 가이드

## 개요

Distance Table은 모든 thought 페어의 유사도를 사전 계산하여 조회 성능을 600배 개선합니다 (60초+ → 0.1초).

## 1단계: SQL 마이그레이션 실행

### 준비사항
- Supabase Dashboard 접속
- SQL Editor 열기
- `thought_units` 테이블에 임베딩 데이터 존재 확인

### 실행 순서

#### 1.1. Distance Table 스키마 생성
```sql
-- 파일: backend/docs/supabase_migrations/010_create_distance_table.sql
-- 실행 시간: <1초
-- 설명: thought_pair_distances 테이블 및 인덱스 생성
```

Supabase SQL Editor에서 `010_create_distance_table.sql` 전체 내용을 복사하여 실행하세요.

**검증:**
```sql
-- 테이블 존재 확인
SELECT EXISTS (
    SELECT FROM information_schema.tables
    WHERE table_name = 'thought_pair_distances'
);
-- Expected: true

-- 인덱스 확인
SELECT indexname FROM pg_indexes
WHERE tablename = 'thought_pair_distances';
-- Expected: idx_tpd_similarity_range, idx_tpd_thought_a, idx_tpd_thought_b
```

#### 1.2. 초기 구축 RPC 함수 생성
```sql
-- 파일: backend/docs/supabase_migrations/011_build_distance_table_rpc.sql
-- 실행 시간: <1초
-- 설명: build_distance_table_batch() 함수 생성
```

Supabase SQL Editor에서 `011_build_distance_table_rpc.sql` 전체 내용을 복사하여 실행하세요.

**검증:**
```sql
-- 함수 존재 확인
SELECT EXISTS (
    SELECT FROM pg_proc
    WHERE proname = 'build_distance_table_batch'
);
-- Expected: true
```

#### 1.3. 증분 갱신 RPC 함수 생성
```sql
-- 파일: backend/docs/supabase_migrations/012_incremental_update_rpc.sql
-- 실행 시간: <1초
-- 설명: update_distance_table_incremental() 함수 생성
```

Supabase SQL Editor에서 `012_incremental_update_rpc.sql` 전체 내용을 복사하여 실행하세요.

**검증:**
```sql
-- 함수 존재 확인
SELECT EXISTS (
    SELECT FROM pg_proc
    WHERE proname = 'update_distance_table_incremental'
);
-- Expected: true
```

## 2단계: Distance Table 초기 구축

### 방법 A: Python API 사용 (권장)

```bash
# FastAPI 서버 실행
cd backend
uvicorn main:app --reload

# 다른 터미널에서 API 호출
curl -X POST "http://localhost:8000/pipeline/distance-table/build?batch_size=50"
```

**예상 결과:**
```json
{
  "success": true,
  "message": "Distance table build started in background (~7min, batch_size=50)"
}
```

**진행 상황 확인:**
```bash
# 로그 확인 (FastAPI 서버 터미널)
# 출력 예시:
# INFO: Batch progress: 5.1% (100/1921), pairs: 38405, duration: 10.2s
# INFO: Batch progress: 10.2% (200/1921), pairs: 38355, duration: 10.1s
# ...

# 상태 조회 API
curl -X GET "http://localhost:8000/pipeline/distance-table/status"
```

**예상 완료 시간:**
- 1,921개 thoughts: ~7분
- 5,000개 thoughts: ~20분
- 10,000개 thoughts: ~80분

### 방법 B: SQL 직접 실행 (테스트용)

**주의:** 60초 타임아웃 위험이 있으므로 소규모 데이터셋(<500개)에서만 사용하세요.

```sql
-- 단일 배치 테스트 (첫 50개)
SELECT build_distance_table_batch(0, 50);

-- 결과 예시:
-- {
--   "success": true,
--   "pairs_inserted": 38450,
--   "batch_offset": 0,
--   "batch_size": 50,
--   "duration_ms": 10234
-- }
```

## 3단계: 데이터 검증

### 3.1. 기본 검증

```sql
-- 전체 페어 개수 확인
SELECT COUNT(*) as total_pairs
FROM thought_pair_distances;
-- Expected: ~1,846,210 (1,921 thoughts 기준)
-- 공식: n × (n-1) / 2

-- 유사도 범위 확인
SELECT
    MIN(similarity) as min_sim,
    MAX(similarity) as max_sim,
    AVG(similarity) as avg_sim,
    COUNT(*) as total_pairs
FROM thought_pair_distances;
-- Expected:
-- min_sim: 0.0~0.1
-- max_sim: 0.9~1.0
-- avg_sim: 0.3~0.4
```

### 3.2. 데이터 정합성 검증

```sql
-- 중복 페어 검증 (0개여야 함)
SELECT thought_a_id, thought_b_id, COUNT(*)
FROM thought_pair_distances
GROUP BY thought_a_id, thought_b_id
HAVING COUNT(*) > 1;
-- Expected: 0 rows

-- 정렬 제약 검증 (thought_a_id < thought_b_id)
SELECT COUNT(*) as invalid_pairs
FROM thought_pair_distances
WHERE thought_a_id >= thought_b_id;
-- Expected: 0

-- 유사도 범위 검증 [0, 1]
SELECT COUNT(*) as out_of_range
FROM thought_pair_distances
WHERE similarity < 0 OR similarity > 1;
-- Expected: 0
```

### 3.3. 성능 벤치마크

```sql
-- Distance Table 조회 성능 (예상 0.1초)
EXPLAIN ANALYZE
SELECT * FROM thought_pair_distances
WHERE similarity BETWEEN 0.057 AND 0.093
ORDER BY similarity ASC
LIMIT 20000;
-- Expected execution time: <100ms

-- v4 비교 (예상 60초+)
EXPLAIN ANALYZE
SELECT * FROM find_similar_pairs_topk(0.0, 1.0, 20, 20000);
-- Expected execution time: 60,000ms+
```

## 4단계: API 통합 테스트

### 4.1. collect-candidates 테스트

```bash
# Distance Table 사용 (기본값)
curl -X POST "http://localhost:8000/pipeline/collect-candidates?strategy=p10_p40&use_distance_table=true"
```

**예상 결과:**
```json
{
  "success": true,
  "total_collected": 48000,
  "query_method": "distance_table",
  "similarity_range": {
    "min": 0.057,
    "max": 0.093
  },
  "execution_time_seconds": 0.1
}
```

### 4.2. 범위 검증 테스트

```bash
# 정상 범위 (30% 범위, 통과)
curl -X POST "http://localhost:8000/pipeline/collect-candidates?strategy=p10_p40"
# Expected: HTTP 200, 48,000개 수집

# 비정상 범위 (100% 범위, 차단)
curl -X POST "http://localhost:8000/pipeline/collect-candidates?strategy=p0_p100"
# Expected: HTTP 400, "Invalid similarity range..."
```

### 4.3. v4 Fallback 테스트

```bash
# v4 방식 사용 (느림, 60초+)
curl -X POST "http://localhost:8000/pipeline/collect-candidates?strategy=p10_p40&use_distance_table=false"
```

**예상 결과:**
```json
{
  "success": true,
  "total_collected": 10000,
  "query_method": "v4_fallback",
  "execution_time_seconds": 65.2
}
```

## 5단계: 증분 갱신 테스트

### 5.1. 새 메모 추가

```bash
# Step 1: 노션에서 새 메모 import
curl -X POST "http://localhost:8000/pipeline/import-from-notion"

# Step 2: 사고 단위 추출 (auto_update_distance_table=true)
curl -X POST "http://localhost:8000/pipeline/extract-thoughts?auto_update_distance_table=true"
```

**예상 결과:**
```json
{
  "success": true,
  "total_thoughts": 15,
  "distance_table_updated": true,
  "distance_table_result": {
    "success": true,
    "new_thought_count": 15,
    "new_pairs_inserted": 28815,
    "duration_ms": 2345
  }
}
```

### 5.2. 수동 증분 갱신

```bash
# 자동 감지 (파라미터 없음)
curl -X POST "http://localhost:8000/pipeline/distance-table/update"

# 특정 thought IDs 지정
curl -X POST "http://localhost:8000/pipeline/distance-table/update" \
  -H "Content-Type: application/json" \
  -d '{"new_thought_ids": [2001, 2002, 2003]}'
```

## 6단계: 모니터링 및 유지보수

### 6.1. 일일 체크리스트

```sql
-- Distance Table 상태 확인
SELECT
    COUNT(*) as total_pairs,
    MIN(created_at) as oldest_pair,
    MAX(created_at) as newest_pair,
    pg_size_pretty(pg_total_relation_size('thought_pair_distances')) as table_size
FROM thought_pair_distances;
```

### 6.2. 성능 모니터링

```bash
# API 상태 조회
curl -X GET "http://localhost:8000/pipeline/distance-table/status"
```

**예상 응답:**
```json
{
  "success": true,
  "statistics": {
    "total_pairs": 1846210,
    "min_similarity": 0.001,
    "max_similarity": 0.987,
    "avg_similarity": 0.342,
    "table_size_mb": 178
  }
}
```

### 6.3. 재구축 시점

다음 경우 전체 재구축을 권장합니다:

1. **대량 메모 추가**: 신규 1,000개 이상
2. **데이터 정합성 문제**: 중복 페어, 범위 오류 발견
3. **성능 저하**: 조회 시간 0.5초 초과

```bash
# 전체 재구축
# 1. 기존 데이터 삭제
# SQL: DELETE FROM thought_pair_distances;

# 2. 재구축 실행
curl -X POST "http://localhost:8000/pipeline/distance-table/build?batch_size=50"
```

## 트러블슈팅

### 문제 1: "Function build_distance_table_batch does not exist"

**원인:** RPC 함수가 생성되지 않음

**해결:**
```sql
-- 011_build_distance_table_rpc.sql 다시 실행
```

### 문제 2: "Timeout after 60 seconds"

**원인:** batch_size가 너무 큼

**해결:**
```bash
# batch_size를 25로 줄이기
curl -X POST "http://localhost:8000/pipeline/distance-table/build?batch_size=25"
```

### 문제 3: "Invalid similarity range"

**원인:** 80% 초과 범위 요청

**해결:**
- 표준 전략 사용: p10_p40, p30_p60, p0_p30
- 커스텀 범위는 80% 이하로 제한

### 문제 4: "Memory explosion"

**원인:** 100,000개 상한선 초과

**해결:**
- 이미 코드에서 `.limit(100000)` 적용되어 있음
- 로그 확인하여 실제 수집 개수 모니터링

## 성능 요약

| 항목 | v4 (기존) | Distance Table (신규) | 개선율 |
|------|----------|-----------------------|--------|
| 조회 시간 | 60초+ | 0.1초 | 600배 |
| 수집 후보 | 10,000개 | 100,000개 (80% 범위 검증) | 10배 |
| 초기 구축 | N/A | 7분 (1회) | N/A |
| 증분 갱신 | 불가능 | 2초/10개 | N/A |
| 저장 공간 | 0 | 178MB | -178MB |
| Break-even | N/A | 7회 조회 | N/A |

## 다음 단계

1. ✅ SQL 마이그레이션 완료
2. ✅ Distance Table 초기 구축 완료
3. ✅ API 통합 테스트 완료
4. ⏭️ 프론트엔드 통합 (선택사항)
5. ⏭️ 모니터링 대시보드 추가 (선택사항)

## 참고 문서

- 플랜 파일: `/.claude/plans/temporal-zooming-kahan.md`
- 마이그레이션 파일: `backend/docs/supabase_migrations/010-012_*.sql`
- Python 서비스: `backend/services/distance_table_service.py`
- API 라우터: `backend/routers/pipeline.py`
