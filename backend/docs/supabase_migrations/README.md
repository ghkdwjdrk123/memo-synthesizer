# Supabase SQL Migrations

이 디렉토리는 Supabase에 배포할 SQL 함수 및 마이그레이션 파일을 관리합니다.

## 배포 방법

### 1. Supabase Dashboard 접속
1. [Supabase Dashboard](https://app.supabase.com) 로그인
2. 프로젝트 선택: `zqrbrddmwrpogabizton`
3. 좌측 메뉴: **SQL Editor** 클릭

### 2. SQL 함수 배포
1. SQL Editor에서 **New Query** 버튼 클릭
2. 마이그레이션 파일 (`001_get_changed_pages.sql`) 전체 내용 복사
3. 에디터에 붙여넣기
4. **Run** 버튼 클릭 (또는 Ctrl+Enter)
5. 성공 메시지 확인

### 3. 배포 확인
다음 쿼리로 함수가 제대로 생성되었는지 확인:

```sql
-- 함수 존재 여부 확인
SELECT * FROM pg_proc WHERE proname = 'get_changed_pages';

-- 함수 테스트 (빈 배열)
SELECT get_changed_pages('[]'::jsonb);
-- 예상 결과: {"new_page_ids": [], "updated_page_ids": [], ...}

-- 함수 테스트 (샘플 데이터)
SELECT get_changed_pages('[
  {"id": "test-id-1", "last_edited": "2024-01-15T14:30:00+00:00"}
]'::jsonb);
-- 예상 결과: {"new_page_ids": ["test-id-1"], ...} (DB에 없는 ID이므로 new)
```

### 4. 애플리케이션에서 검증
서버 시작 시 자동으로 RPC 함수 가용성을 체크합니다:

```bash
cd backend
uvicorn main:app --reload
```

로그에서 다음 메시지 확인:
```
✅ RPC function 'get_changed_pages' is available and working
```

만약 함수가 없으면:
```
⚠️  RPC function 'get_changed_pages' not available
   Import will use fallback mode (full table scan)
```

## 마이그레이션 파일 목록

### 001_get_changed_pages.sql
- **목적**: 증분 import를 위한 변경 감지 함수
- **생성일**: 2024-01-15
- **사용처**: `backend/services/supabase_service.py` → `get_pages_to_fetch()`
- **성능**:
  - 변경 감지 시간: ~0.2초 (일정, 100k 페이지까지 확장 가능)
  - 정확도: 100% (unchanged pages 정확히 감지)
- **Fallback**: RPC 실패 시 자동으로 전체 테이블 스캔으로 전환

### 002_soft_delete_support.sql
- **목적**: raw_notes 테이블에 소프트 삭제 지원 추가
- **생성일**: 2024-01-16
- **주요 변경**: is_deleted, deleted_at 컬럼 추가

### 003_add_delete_detection.sql
- **목적**: get_changed_pages RPC 확장 - 삭제 감지 기능 추가
- **생성일**: 2024-01-16
- **주요 변경**: deleted_page_ids 반환 추가

### 004_create_hnsw_index.sql
- **목적**: pgvector HNSW 인덱스 생성 (IVFFlat → HNSW)
- **생성일**: 2024-01-22
- **성능**: 유사도 검색 속도 개선

### 005_v4_accuracy_first.sql
- **목적**: 후보 페어 검색 RPC (정확도 우선)
- **생성일**: 2024-01-29
- **성능**: ~60초+ (1,921개 기준)
- **주의**: Distance Table 사용 시 이 함수는 fallback으로만 사용됨

### 006_create_pair_candidates.sql
- **목적**: pair_candidates 테이블 생성 (후보 페어 저장)
- **생성일**: 2024-01-26
- **주요 변경**: 후보 페어 및 AI 평가 점수 저장

### 007_extend_thought_pairs.sql
- **목적**: thought_pairs 테이블 확장 (AI 평가 필드 추가)
- **생성일**: 2024-01-26
- **주요 변경**: ai_score, connection_reason 등 추가

### 008_create_similarity_distribution_cache.sql
- **목적**: 유사도 분포 캐시 테이블 생성
- **생성일**: 2024-01-29
- **용도**: 백분위수 계산 캐싱

### 009_v2_optimized_distribution.sql
- **목적**: 유사도 분포 계산 RPC (최적화 버전)
- **생성일**: 2024-01-29
- **성능**: 분포 계산 속도 개선

### 010_create_distance_table.sql ⭐
- **목적**: Distance Table 스키마 생성 (모든 thought 페어의 유사도 사전 계산)
- **생성일**: 2026-01-29
- **테이블**: `thought_pair_distances`
- **성능**: 조회 0.1초 (vs v4 60초+), **600배 개선**
- **저장 공간**: 1,921개 기준 178MB (테이블 118MB + 인덱스 60MB)
- **Break-even**: 7회 조회부터 이득
- **주요 인덱스**:
  - `idx_tpd_similarity_range`: 유사도 범위 조회 최적화 (핵심!)
  - `idx_tpd_thought_a`, `idx_tpd_thought_b`: thought 기반 조회

### 011_build_distance_table_rpc.sql ⭐
- **목적**: Distance Table 초기 구축 - 단일 배치 처리 RPC 함수
- **생성일**: 2026-01-29
- **함수**: `build_distance_table_batch(batch_offset, batch_size)`
- **성능**: 각 배치 ~10초 (batch_size=50), Python에서 순차 호출
- **총 시간**: 1,921개 기준 39회 호출 → ~7분
- **사용법**:
  ```python
  # Python 순차 호출 예시
  for offset in range(0, 1921, 50):
      await client.rpc("build_distance_table_batch", {
          "batch_offset": offset,
          "batch_size": 50
      })
  ```
- **Trade-offs**: 순차 처리 (안정성 우선, DB 부하 안정, 연결 제한 회피)

### 012_incremental_update_rpc.sql ⭐
- **목적**: Distance Table 증분 갱신 RPC 함수
- **생성일**: 2026-01-29
- **함수**: `update_distance_table_incremental(new_thought_ids)`
- **성능**: 10개 신규 thought → ~2초
- **자동 감지**: new_thought_ids=NULL 시 thought_pair_distances에 없는 thought 자동 감지
- **갱신 로직**:
  1. 신규 × 기존 페어 생성 (O(신규 × 기존))
  2. 신규 × 신규 페어 생성 (신규 2개 이상일 때)
- **자동 호출 시점**:
  - `/pipeline/extract-thoughts` 완료 후 (auto_update_distance_table=true)
  - 신규 thought 10개 이상일 때 자동 실행
- **수동 트리거**: `/pipeline/distance-table/update`

## 롤백 방법

함수를 제거하려면:

```sql
DROP FUNCTION IF EXISTS get_changed_pages(jsonb);
```

**주의**: 함수를 삭제하면 애플리케이션은 자동으로 Fallback 모드로 작동합니다 (성능 저하 없음).

## 트러블슈팅

### 문제: 함수 실행 시 에러
**증상**: SQL 함수 실행 시 오류 발생

**해결**:
1. 에러 메시지 확인
2. `raw_notes` 테이블 존재 확인: `SELECT * FROM raw_notes LIMIT 1;`
3. 인덱스 존재 확인: `SELECT * FROM pg_indexes WHERE tablename = 'raw_notes';`

### 문제: 함수가 느림
**증상**: 함수 실행 시간이 1초 이상

**해결**:
1. 인덱스 확인: `idx_raw_notes_notion_page_id`가 있는지 확인
2. VACUUM 실행: `VACUUM ANALYZE raw_notes;`
3. 쿼리 플랜 확인: `EXPLAIN ANALYZE SELECT * FROM raw_notes WHERE notion_page_id = 'test';`

### 문제: Python에서 RPC 호출 실패
**증상**: `function get_changed_pages does not exist`

**해결**:
1. 함수 배포 확인 (위의 "배포 확인" 참고)
2. Supabase 프로젝트 확인 (올바른 프로젝트인지)
3. API Key 권한 확인 (RPC 호출 권한 있는지)

## Distance Table 사용 가이드

### 초기 설정 (한 번만 실행)

1. **마이그레이션 실행** (Supabase SQL Editor)
   ```sql
   -- 1. 테이블 생성
   -- 010_create_distance_table.sql 실행

   -- 2. RPC 함수 생성
   -- 011_build_distance_table_rpc.sql 실행
   -- 012_incremental_update_rpc.sql 실행
   ```

2. **Distance Table 초기 구축** (API 호출)
   ```bash
   POST http://localhost:8000/pipeline/distance-table/build?batch_size=50
   # 예상 시간: ~7분 (1,921개 기준)
   ```

3. **통계 확인**
   ```bash
   GET http://localhost:8000/pipeline/distance-table/status
   # 응답: total_pairs, min/max/avg similarity
   ```

### 일상적 사용

**자동 갱신 (권장)**
- `/pipeline/extract-thoughts` 실행 시 자동으로 Distance Table 갱신
- 신규 thought 10개 이상일 때 자동 실행
- 파라미터: `auto_update_distance_table=true` (기본값)

**수동 갱신**
```bash
POST http://localhost:8000/pipeline/distance-table/update
# 신규 thought가 있으면 자동 감지하여 갱신
```

**후보 수집 (Distance Table 사용)**
```bash
POST http://localhost:8000/pipeline/collect-candidates?use_distance_table=true
# 조회 시간: 0.1초 (권장)
# fallback: use_distance_table=false (v4 RPC, 60초+)
```

## 참고 자료

- [Supabase Database Functions](https://supabase.com/docs/guides/database/functions)
- [PostgreSQL PL/pgSQL Documentation](https://www.postgresql.org/docs/current/plpgsql.html)
- [Distance Table Service](/Users/hwangjeongtae/Desktop/develop_project/notion_idea_synthesizer/memo-synthesizer/backend/services/distance_table_service.py) - Python 구현
- [Plan 파일](/Users/hwangjeongtae/.claude/plans/majestic-gliding-adleman.md) - 증분 import 구현 상세
