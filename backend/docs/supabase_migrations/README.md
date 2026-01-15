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
  - 현재 (726 pages): ~100ms
  - 확장 (10,000 pages): ~150ms
- **Fallback**: RPC 실패 시 자동으로 전체 테이블 스캔으로 전환

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

## 참고 자료

- [Supabase Database Functions](https://supabase.com/docs/guides/database/functions)
- [PostgreSQL PL/pgSQL Documentation](https://www.postgresql.org/docs/current/plpgsql.html)
- [Plan 파일](/Users/hwangjeongtae/.claude/plans/majestic-gliding-adleman.md) - 구현 상세 분석
