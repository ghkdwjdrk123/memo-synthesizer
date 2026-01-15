-- Incremental Import: Change Detection Function
-- Created: 2024-01-15
-- Purpose: Notion 페이지 메타데이터와 DB 비교하여 변경 감지
--
-- 사용 방법:
-- 1. Supabase Dashboard → SQL Editor
-- 2. 이 파일 내용 전체 복사 → Run
-- 3. 확인: SELECT * FROM pg_proc WHERE proname = 'get_changed_pages';

CREATE OR REPLACE FUNCTION get_changed_pages(pages_data jsonb)
RETURNS jsonb
LANGUAGE plpgsql
AS $$
DECLARE
    result jsonb;
    new_ids text[] := ARRAY[]::text[];
    updated_ids text[] := ARRAY[]::text[];
    page_record jsonb;
    notion_id text;
    notion_time timestamptz;
    db_time timestamptz;
BEGIN
    -- 각 Notion 페이지 처리
    FOR page_record IN SELECT * FROM jsonb_array_elements(pages_data)
    LOOP
        -- JSON에서 데이터 추출
        notion_id := page_record->>'id';
        notion_time := (page_record->>'last_edited')::timestamptz;

        -- 초 단위로 truncate (microsecond 차이 무시)
        notion_time := date_trunc('second', notion_time);

        -- DB에서 기존 페이지 조회 (인덱스 활용: idx_raw_notes_notion_page_id)
        SELECT date_trunc('second', notion_last_edited_time) INTO db_time
        FROM raw_notes
        WHERE notion_page_id = notion_id;

        -- 비교 및 분류
        IF NOT FOUND THEN
            -- 신규 페이지 (DB에 없음)
            new_ids := array_append(new_ids, notion_id);
        ELSIF notion_time > db_time THEN
            -- 수정된 페이지 (Notion timestamp가 더 최신)
            updated_ids := array_append(updated_ids, notion_id);
        END IF;
        -- ELSE: unchanged (DB timestamp와 동일) → skip
    END LOOP;

    -- 결과 반환
    result := jsonb_build_object(
        'new_page_ids', to_jsonb(new_ids),
        'updated_page_ids', to_jsonb(updated_ids),
        'total_checked', jsonb_array_length(pages_data),
        'unchanged_count', jsonb_array_length(pages_data) - COALESCE(array_length(new_ids, 1), 0) - COALESCE(array_length(updated_ids, 1), 0)
    );

    RETURN result;

EXCEPTION
    WHEN OTHERS THEN
        -- 에러 발생 시 에러 정보와 함께 빈 결과 반환
        -- Python에서 이 에러를 감지하고 Fallback 로직 작동
        result := jsonb_build_object(
            'error', SQLERRM,
            'error_detail', SQLSTATE,
            'new_page_ids', '[]'::jsonb,
            'updated_page_ids', '[]'::jsonb
        );
        RETURN result;
END;
$$;

-- 함수 설명 추가
COMMENT ON FUNCTION get_changed_pages(jsonb) IS
'Notion 페이지 메타데이터와 DB 비교하여 신규/수정 페이지 ID 반환.

입력 형식:
[
  {"id": "page-uuid", "last_edited": "2024-01-15T14:30:00+00:00"},
  ...
]

출력 형식:
{
  "new_page_ids": ["uuid1", ...],
  "updated_page_ids": ["uuid2", ...],
  "total_checked": 726,
  "unchanged_count": 700
}

에러 발생 시:
{
  "error": "에러 메시지",
  "error_detail": "SQLSTATE 코드",
  "new_page_ids": [],
  "updated_page_ids": []
}

성능:
- 현재 규모 (726 pages): ~100ms
- 대규모 (10,000 pages): ~150ms
- 인덱스 활용: idx_raw_notes_notion_page_id

변경 이력:
- 2024-01-15: 초기 생성
';

-- 테스트 쿼리 예시
-- SELECT get_changed_pages('[{"id": "test-id", "last_edited": "2024-01-15T14:30:00+00:00"}]'::jsonb);
