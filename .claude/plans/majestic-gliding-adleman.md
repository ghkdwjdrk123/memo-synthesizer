# Plan: Incremental Import - Fetch Only Changed Pages

## Problem Statement

### Current Implementation Issues
**현재 방식의 비효율성:**
- 매번 **전체 724개 페이지를 9분간 처리**
- 731번째 페이지를 추가하면 → 전체 725개 재처리
- 500번째 페이지 수정하면 → 전체 724개 재처리
- **변경 감지 없음**: 수정 여부와 관계없이 무조건 전체 fetch

**성능 문제:**
- Import 시간: 9분 (724 pages × 3 req/sec rate limit)
- API 호출 낭비: 대부분의 페이지는 변경되지 않음
- DB 쓰기 부하: 불필요한 upsert 반복

### User's Proposed Solution (Incremental Update)

**개선 제안 프로세스:**
```
1. 부모 페이지에서 하위 페이지 메타데이터 가져오기 (lightweight)
   ↓
2. 각 페이지의 notion_last_edited_time 확인
   ↓
3. DB의 raw_notes 테이블과 비교
   ↓
4. 변경 감지:
   - 신규 페이지 (DB에 없음) → fetch 대상
   - 수정된 페이지 (last_edited_time 다름) → fetch 대상
   - 미수정 페이지 (last_edited_time 같음) → SKIP
   ↓
5. fetch 대상 페이지만 API 호출하여 content 가져오기
   ↓
6. 대상 페이지만 upsert
```

**예상 효과:**
- 초기 import: 724개 전체 (9분) - 불가피
- 1개 추가 시: 1개만 fetch (<1초)
- 10개 수정 시: 10개만 fetch (~3초)
- 100% 절감: 변경 없을 시 0개 fetch (즉시 완료)

---

## Technical Analysis

### Phase 1: Notion API Metadata Fetching

**현재 코드 분석:**
```python
# backend/services/notion_service.py:324
async def fetch_child_pages_from_parent(...) -> List[Dict]:
    """Fetch all child pages from parent page."""
    pages = []
    has_more = True
    start_cursor = None

    while has_more:
        response = await self.client.blocks.children.list(
            block_id=parent_page_id,
            page_size=page_size,
            start_cursor=start_cursor
        )
        pages.extend(response.get("results", []))
        # ...

    return pages  # ✅ 이미 metadata 포함 (id, last_edited_time, properties)
```

**현재 반환 데이터 구조:**
```json
{
  "id": "abc-123",
  "created_time": "2024-01-01T12:00:00.000Z",
  "last_edited_time": "2024-01-15T14:30:00.000Z",  // ✅ 비교 키
  "properties": {
    "제목": "페이지 제목",
    // ... 기타 properties
  },
  "url": "https://notion.so/abc-123"
}
```

**✅ 결론:**
- Notion API는 이미 `last_edited_time`을 반환
- 추가 API 호출 불필요
- 메타데이터는 lightweight (content 없음)

---

### Phase 2: Database Comparison Strategy

**DB 스키마:**
```sql
CREATE TABLE raw_notes (
    id UUID PRIMARY KEY,
    notion_page_id TEXT UNIQUE NOT NULL,  -- ✅ 비교 키
    notion_last_edited_time TIMESTAMPTZ NOT NULL,  -- ✅ 비교 키
    content TEXT,
    properties_json JSONB,
    imported_at TIMESTAMPTZ DEFAULT NOW()
);
```

**비교 쿼리 설계:**

**Option A: Single Query with LEFT JOIN (추천)**
```python
async def get_pages_to_fetch(
    self,
    notion_pages: List[Dict]
) -> tuple[List[str], List[str]]:
    """
    Returns (new_page_ids, updated_page_ids).

    Args:
        notion_pages: List of page metadata from Notion API

    Returns:
        new_page_ids: Pages not in DB
        updated_page_ids: Pages in DB but last_edited_time differs
    """
    page_map = {
        p["id"]: datetime.fromisoformat(p["last_edited_time"].replace("Z", "+00:00"))
        for p in notion_pages
    }

    # Fetch existing pages from DB
    existing = await self.client.table("raw_notes").select(
        "notion_page_id, notion_last_edited_time"
    ).in_("notion_page_id", list(page_map.keys())).execute()

    existing_map = {
        row["notion_page_id"]: row["notion_last_edited_time"]
        for row in existing.data
    }

    new_page_ids = []
    updated_page_ids = []

    for page_id, notion_time in page_map.items():
        if page_id not in existing_map:
            new_page_ids.append(page_id)  # 신규
        elif existing_map[page_id] != notion_time:
            updated_page_ids.append(page_id)  # 수정됨
        # else: 변경 없음 → SKIP

    return new_page_ids, updated_page_ids
```

**성능:**
- DB 쿼리: 1회 (WHERE IN 사용)
- 메모리: O(n) - 724개 페이지 메타데이터만
- 시간 복잡도: O(n) - 단순 비교

**Option B: Batch Query (대안)**
```sql
-- PostgreSQL의 경우
WITH notion_data(page_id, edited_time) AS (
    VALUES
        ('id1', '2024-01-15 14:30:00+00'::timestamptz),
        ('id2', '2024-01-15 15:00:00+00'::timestamptz),
        ...
)
SELECT
    nd.page_id,
    CASE
        WHEN rn.notion_page_id IS NULL THEN 'new'
        WHEN rn.notion_last_edited_time < nd.edited_time THEN 'updated'
        ELSE 'unchanged'
    END as status
FROM notion_data nd
LEFT JOIN raw_notes rn ON rn.notion_page_id = nd.page_id;
```

**✅ 추천:** Option A (Python 레벨 비교)
- 이유: Supabase Python SDK와 호환성 좋음
- SQL보다 디버깅 쉬움
- 성능 차이 미미 (724개 정도)

---

### Phase 3: Selective Content Fetching

**현재 로직 (전체 fetch):**
```python
# backend/routers/pipeline.py:119-157
for idx, page in enumerate(pages, 1):  # 724번 반복
    page_id = page.get("id")
    fetched_content = await _fetch_page_with_retry(notion_service, page_id, max_retries=3)
    # ... upsert
```

**개선 로직 (선택적 fetch):**
```python
# 1. 메타데이터 가져오기 (lightweight)
all_pages = await notion_service.fetch_child_pages_from_parent(...)

# 2. 변경 감지
new_ids, updated_ids = await supabase_service.get_pages_to_fetch(all_pages)
fetch_targets = new_ids + updated_ids

logger.info(
    f"Change detection: {len(new_ids)} new, {len(updated_ids)} updated, "
    f"{len(all_pages) - len(fetch_targets)} unchanged (skipped)"
)

# 3. 대상만 fetch (변경된 페이지만)
for page in all_pages:
    page_id = page.get("id")

    if page_id not in fetch_targets:
        await supabase_service.increment_job_progress(job_id, skipped=True)
        continue  # ✅ SKIP

    # Fetch content only for changed pages
    fetched_content = await _fetch_page_with_retry(notion_service, page_id, max_retries=3)
    # ... upsert
    await supabase_service.increment_job_progress(job_id, imported=True)
```

**성능 비교:**

| 시나리오 | 현재 방식 | 개선 방식 | 절감율 |
|---------|----------|----------|-------|
| 초기 import (724개 전체 신규) | 9분 | 9분 | 0% |
| 1개 추가 | 9분 | <1초 | 99.8% |
| 10개 수정 | 9분 | ~3초 | 99.4% |
| 100개 수정 | 9분 | ~30초 | 94.4% |
| 변경 없음 (재실행) | 9분 | <1초 | 99.9% |

---

## Implementation Plan

### Phase 1: Add Change Detection Method (30 min)

**File:** `backend/services/supabase_service.py` (ADD after line 880)

**New Method:**
```python
async def get_pages_to_fetch(
    self,
    notion_pages: List[Dict[str, Any]]
) -> tuple[List[str], List[str]]:
    """
    Compare Notion pages with DB to detect changes.

    Args:
        notion_pages: List of page metadata from Notion API
            Each page must have: id, last_edited_time

    Returns:
        Tuple of (new_page_ids, updated_page_ids)
        - new_page_ids: Pages not in raw_notes table
        - updated_page_ids: Pages with different last_edited_time

    Example:
        >>> pages = [{"id": "abc", "last_edited_time": "2024-01-15T14:30:00.000Z"}]
        >>> new, updated = await service.get_pages_to_fetch(pages)
        >>> print(f"New: {len(new)}, Updated: {len(updated)}")
    """
    await self._ensure_initialized()

    # Build map: page_id -> last_edited_time (from Notion)
    page_map = {}
    for p in notion_pages:
        page_id = p.get("id")
        last_edited = p.get("last_edited_time")

        if not page_id or not last_edited:
            logger.warning(f"Page missing id or last_edited_time: {p}")
            continue

        # Parse ISO 8601 timestamp
        notion_time = datetime.fromisoformat(last_edited.replace("Z", "+00:00"))
        page_map[page_id] = notion_time

    if not page_map:
        logger.warning("No valid pages to check")
        return [], []

    # Fetch existing pages from DB
    try:
        response = await (
            self.client.table("raw_notes")
            .select("notion_page_id, notion_last_edited_time")
            .in_("notion_page_id", list(page_map.keys()))
            .execute()
        )

        existing_map = {
            row["notion_page_id"]: row["notion_last_edited_time"]
            for row in response.data
        }

    except Exception as e:
        logger.error(f"Failed to fetch existing pages: {e}")
        # On error, treat all as new (safe fallback)
        return list(page_map.keys()), []

    # Compare timestamps
    new_page_ids = []
    updated_page_ids = []

    for page_id, notion_time in page_map.items():
        if page_id not in existing_map:
            new_page_ids.append(page_id)
        else:
            db_time = existing_map[page_id]

            # Parse DB timestamp (may be string or datetime)
            if isinstance(db_time, str):
                db_time = datetime.fromisoformat(db_time.replace("Z", "+00:00"))

            # Compare (notion_time > db_time means updated)
            if notion_time > db_time:
                updated_page_ids.append(page_id)
            # else: unchanged, skip

    logger.info(
        f"Change detection: {len(new_page_ids)} new, {len(updated_page_ids)} updated, "
        f"{len(page_map) - len(new_page_ids) - len(updated_page_ids)} unchanged"
    )

    return new_page_ids, updated_page_ids
```

**Estimated Lines:** ~70 lines

---

### Phase 2: Update Background Task Logic (45 min)

**File:** `backend/routers/pipeline.py` (MODIFY lines 118-157)

**Changes:**

**Before:**
```python
# Process each page with content fetching (RESTORED LOGIC)
for idx, page in enumerate(pages, 1):
    page_id = page.get("id")
    try:
        fetched_content = await _fetch_page_with_retry(notion_service, page_id, max_retries=3)
        # ... rest of logic
```

**After:**
```python
# Process each page with INCREMENTAL content fetching
# 1. Detect changes
new_page_ids, updated_page_ids = await supabase_service.get_pages_to_fetch(pages)
fetch_targets = set(new_page_ids + updated_page_ids)

logger.info(
    f"[Job {job_id}] Incremental import: "
    f"{len(new_page_ids)} new, {len(updated_page_ids)} updated, "
    f"{len(pages) - len(fetch_targets)} unchanged (will skip)"
)

# 2. Process only changed pages
for idx, page in enumerate(pages, 1):
    page_id = page.get("id")

    try:
        # Skip unchanged pages
        if page_id not in fetch_targets:
            logger.info(f"[Job {job_id}] [{idx}/{total_count}] ⏭️  Skipped (unchanged): {page_id}")
            await supabase_service.increment_job_progress(job_id, skipped=True)
            continue

        # Fetch content only for new/updated pages
        fetched_content = None
        if mode == "parent_page":
            try:
                fetched_content = await _fetch_page_with_retry(notion_service, page_id, max_retries=3)
                # ... rest of existing logic (title fallback, etc.)
```

**Key Changes:**
1. Call `get_pages_to_fetch()` before loop
2. Convert to set for O(1) lookup
3. Add skip logic with logging
4. Update progress counters (skipped)

**Estimated Lines:** +15 lines, modified 5 lines

---

### Phase 3: Update Progress Tracking (15 min)

**File:** `backend/services/supabase_service.py` (MODIFY line 850)

**Current Signature:**
```python
async def increment_job_progress(
    self,
    job_id: str,
    imported: bool = False,
    failed_page: Optional[Dict[str, str]] = None
) -> None:
```

**Updated Signature:**
```python
async def increment_job_progress(
    self,
    job_id: str,
    imported: bool = False,
    skipped: bool = False,  # ✅ NEW
    failed_page: Optional[Dict[str, str]] = None
) -> None:
    """Increment job progress. Never raises exceptions."""
    try:
        job = await self.get_import_job(job_id)
        updates = {"processed_pages": job["processed_pages"] + 1}

        if imported:
            updates["imported_pages"] = job["imported_pages"] + 1
        if skipped:
            updates["skipped_pages"] = job["skipped_pages"] + 1  # ✅ NEW
        if failed_page:
            current_failed = job.get("failed_pages", [])
            current_failed.append(failed_page)
            updates["failed_pages"] = current_failed

        await self.client.table("import_jobs").update(updates).eq("id", job_id).execute()

    except Exception as e:
        logger.error(f"Failed to increment job {job_id} progress: {e}")
```

**Estimated Lines:** +3 lines modified

---

### Phase 4: Update Job Status Response (10 min)

**File:** `backend/routers/pipeline.py` (MODIFY get_import_status endpoint)

**Add to response:**
```python
return ImportJobStatus(
    job_id=job["id"],
    status=job["status"],
    # ... existing fields ...
    skipped_pages=job.get("skipped_pages", 0),  # ✅ Already exists
    # New computed fields:
    new_pages=job.get("new_pages", 0),  # Optional: track separately
    updated_pages=job.get("updated_pages", 0),  # Optional
)
```

**Note:** `skipped_pages` already exists in current schema, no changes needed.

---

## Testing Strategy

### Test Case 1: Initial Import (No Existing Data)
**Scenario:** First time importing all pages

**Expected Behavior:**
- All 724 pages marked as "new"
- All 724 pages fetched
- Time: ~9 minutes
- DB: 724 rows inserted

**Test Command:**
```bash
curl -X POST "http://localhost:8000/pipeline/import-from-notion?page_size=100"
# Wait for completion
# Verify: 724 imported, 0 skipped
```

---

### Test Case 2: Re-import Without Changes
**Scenario:** Run import again immediately (no edits in Notion)

**Expected Behavior:**
- 0 pages new
- 0 pages updated
- 724 pages skipped
- Time: <5 seconds (no API calls to fetch_page_blocks)
- DB: No writes

**Test Command:**
```bash
# Run import again
curl -X POST "http://localhost:8000/pipeline/import-from-notion?page_size=100"
# Verify: 0 imported, 724 skipped
```

**Verification:**
```python
# Check job status
response = requests.get(f"http://localhost:8000/pipeline/import-status/{job_id}")
assert response.json()["imported_pages"] == 0
assert response.json()["skipped_pages"] == 724
```

---

### Test Case 3: Single Page Added
**Scenario:** Add 1 new page in Notion, then import

**Setup:**
1. Manually create new page in Notion parent
2. Wait 1 minute (Notion indexing)
3. Run import

**Expected Behavior:**
- 1 page new
- 0 pages updated
- 724 pages skipped
- Time: <2 seconds (1 API call)
- DB: 1 row inserted

**Verification:**
```sql
SELECT COUNT(*) FROM raw_notes;  -- Should be 725
```

---

### Test Case 4: Multiple Pages Updated
**Scenario:** Edit 10 existing pages in Notion

**Setup:**
1. Manually edit content of 10 pages
2. Wait 1 minute
3. Run import

**Expected Behavior:**
- 0 pages new
- 10 pages updated
- 715 pages skipped (725 - 10)
- Time: ~3 seconds (10 API calls)
- DB: 10 rows updated

**Verification:**
```python
# Check last imported_at timestamps
response = supabase.table("raw_notes").select("notion_page_id, imported_at").order("imported_at", desc=True).limit(10).execute()
# Top 10 should have recent timestamps
```

---

### Test Case 5: Mixed Changes
**Scenario:** 50 new pages, 100 updated pages, 575 unchanged

**Expected Behavior:**
- 50 pages new
- 100 pages updated
- 575 pages skipped
- Time: ~50 seconds (150 API calls)
- DB: 50 inserts, 100 updates

---

## Performance Analysis

### API Call Reduction

**Scenario Analysis:**

| 시나리오 | 변경 페이지 | 현재 API 호출 | 개선 후 API 호출 | 절감 |
|---------|-----------|------------|---------------|-----|
| 초기 import | 724 | 724 | 724 | 0% |
| 1개 추가 | 1 | 724 | 1 | 99.86% |
| 10개 수정 | 10 | 724 | 10 | 98.62% |
| 100개 수정 | 100 | 724 | 100 | 86.19% |
| 변경 없음 | 0 | 724 | 0 | 100% |

**Rate Limit 영향:**
- Notion API: 3 req/sec
- 1개 추가 시: 0.33초 vs 241초 (728배 빠름)
- 10개 수정 시: 3.33초 vs 241초 (72배 빠름)

---

### Database Query Efficiency

**Current:**
- 724 upsert queries (매번)

**Optimized:**
- 1 SELECT query (change detection)
- N upsert queries (N = 변경된 페이지 수만)

**Example:**
```
10개 변경 시:
- 1 SELECT (724개 page_id IN 쿼리) ~50ms
- 10 UPSERT ~200ms
Total: ~250ms vs 현재 724 upsert (~1500ms)
```

---

## Critical Issues & Deep Analysis

### 🚨 Issue 1: Microseconds Precision Mismatch
**Problem Discovery:**
- Notion API: `"2023-02-28T14:29:00.000Z"` (milliseconds precision)
- Supabase DB: `"2023-02-28T14:29:00.123456+00:00"` (microseconds precision)
- 비교 시: `notion_time != db_time` → 불필요한 fetch 발생!

**실제 테스트 결과:**
```python
notion_time = datetime(2023, 2, 28, 14, 29, 0)          # .000
db_time     = datetime(2023, 2, 28, 14, 29, 0, 123456)  # .123456
notion_time == db_time  # False!
```

**Impact:**
- 변경되지 않은 페이지도 "updated"로 인식
- 증분 업데이트 효과 감소 (worst case: 모든 페이지 재fetch)

**Solution: Truncate to Seconds**
```python
# In get_pages_to_fetch()
def truncate_to_seconds(dt: datetime) -> datetime:
    """Remove microseconds for comparison."""
    return dt.replace(microsecond=0)

# Compare timestamps
notion_time_trunc = truncate_to_seconds(notion_time)
db_time_trunc = truncate_to_seconds(db_time)

if notion_time_trunc > db_time_trunc:  # ✅ Now accurate
    updated_page_ids.append(page_id)
```

**Alternative: Use >= instead of >**
```python
# Less strict: consider "equal" as "unchanged"
if notion_time > db_time:  # Only if Notion timestamp is STRICTLY newer
    updated_page_ids.append(page_id)
```

**Recommended:** Truncate to seconds (more robust)

---

### 🚨 Issue 2: Notion API Block Structure Change
**Problem:**
- `fetch_child_pages_from_parent()` uses `blocks.children.list()`
- Returns `child_page` **blocks**, not full page objects
- `last_edited_time`은 **block의 수정 시간**이지 **page content의 수정 시간**이 아닐 수 있음!

**Code Analysis (notion_service.py:454-468):**
```python
for block in blocks:
    if block.get("type") == "child_page":
        child_page_id = block.get("id")
        # ...
        page_data = {
            "id": child_page_id,
            "created_time": block.get("created_time"),        # ✅ Block 생성 시간
            "last_edited_time": block.get("last_edited_time"), # ⚠️ Block 수정 시간
            "properties": {"제목": child_page_title}
        }
```

**Critical Question:**
- `block.get("last_edited_time")` = block metadata 수정 시간?
- vs. **page content 수정 시간**?

**Test Verification Needed:**
1. Notion에서 페이지 A의 내용 수정
2. API 호출하여 `last_edited_time` 확인
3. 내용 수정이 `last_edited_time`에 반영되는지 확인

**If NOT reflected:**
- 증분 업데이트가 작동하지 않음
- Content 변경을 감지 못함
- **Plan 전체가 무효화됨**

**Solution if problem exists:**
- Use `pages.retrieve(page_id)` API to get accurate last_edited_time
- Trade-off: 724 API calls (but no content fetch)
- Still faster than fetching content

---

### 🚨 Issue 3: DB에 저장된 notion_last_edited_time의 출처
**Problem:**
현재 코드에서 `notion_last_edited_time`을 어디서 가져오는지 확인:

**Current code (pipeline.py:179-181):**
```python
notion_last_edited_time=datetime.fromisoformat(
    page.get("last_edited_time").replace("Z", "+00:00")
),
```

**Key Point:**
- `page`는 `fetch_child_pages_from_parent()`의 결과
- 즉, **block의 last_edited_time을 저장**하고 있음
- Content 변경과 무관할 수 있음!

**Verification:**
```python
# Test: 페이지 내용 수정 후
pages = await notion_service.fetch_child_pages_from_parent(parent_id)
for page in pages:
    if page["id"] == "modified_page_id":
        print(f"last_edited_time: {page['last_edited_time']}")
        # 내용 수정 전후 비교
```

---

### 🚨 Issue 4: Timezone Handling Edge Cases
**Problem:**
- Notion API: Always UTC with "Z" suffix
- Supabase TIMESTAMPTZ: May store with different timezone
- Python comparison: timezone-aware vs timezone-naive

**Current Protection:**
```python
notion_time = datetime.fromisoformat(last_edited.replace("Z", "+00:00"))
```

**Additional Edge Case:**
- DB might return timezone-naive datetime
- Comparison will fail: `TypeError: can't compare offset-naive and offset-aware datetimes`

**Solution:**
```python
from datetime import timezone

def ensure_timezone_aware(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware (UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

# In get_pages_to_fetch()
notion_time = ensure_timezone_aware(
    datetime.fromisoformat(last_edited.replace("Z", "+00:00"))
)
db_time = ensure_timezone_aware(existing_map[page_id])
```

---

### 🚨 Issue 5: Race Condition in Multi-Stage Comparison
**Problem Scenario:**
1. Import Job A starts: Fetches metadata (724 pages)
2. User modifies Page X in Notion
3. Import Job A: Compares timestamps (Page X not marked as changed)
4. Import Job A: Skips Page X
5. Result: **Page X update missed**

**Time Window:**
- Metadata fetch: ~2 seconds
- Comparison: ~0.1 seconds
- Content fetch loop: ~9 minutes
- **Total window: 9+ minutes** where changes can be missed

**Frequency:**
- Low for manual edits (unlikely during 9-min window)
- High for automated scripts/integrations

**Solution Options:**

**Option A: Accept eventual consistency**
- Next import will catch the change
- Acceptable for most use cases
- Document in README

**Option B: Re-check before skip**
- When about to skip, fetch latest timestamp from Notion
- Trade-off: Extra API call per skipped page
- Defeats purpose of optimization

**Option C: Timestamp cache invalidation**
- Cache metadata timestamps with TTL (e.g., 5 minutes)
- Refresh if cache expired
- Complex implementation

**Recommended:** Option A (document limitation)

---

### 🚨 Issue 6: Pagination Edge Cases in Change Detection
**Problem:**
`fetch_child_pages_from_parent()` uses pagination:
```python
# Batch 1: Pages 1-100
# Batch 2: Pages 101-200
# ...
```

**Edge Case:**
- During pagination, new page added in Notion
- Pagination cursor may skip or duplicate pages
- Known Notion API limitation

**Impact on Change Detection:**
- New page might be missed (not in initial metadata fetch)
- Or: Same page appears twice (cursor shift)

**Current Protection:**
- UNIQUE constraint on `notion_page_id` in DB
- Duplicate insert will be ignored (upsert)

**Additional Protection Needed:**
```python
# Deduplicate before processing
seen_page_ids = set()
unique_pages = []
for page in all_pages:
    page_id = page["id"]
    if page_id not in seen_page_ids:
        seen_page_ids.add(page_id)
        unique_pages.append(page)
    else:
        logger.warning(f"Duplicate page detected during pagination: {page_id}")

pages = unique_pages
```

---

### 🚨 Issue 7: Memory Consumption for Large Sets
**Current Approach:**
```python
# Load all page_ids into memory
existing = await self.client.table("raw_notes").select(
    "notion_page_id, notion_last_edited_time"
).in_("notion_page_id", list(page_map.keys())).execute()
```

**Problem:**
- 724 pages: ~50KB (negligible)
- 10,000 pages: ~700KB (still OK)
- 100,000 pages: ~7MB (concerning)

**Query Limit:**
- Supabase `.in_()` clause has limit (~1000 items in PostgreSQL)
- 724 pages: ✅ Safe
- 10,000 pages: ❌ Will fail

**Solution: Batch Query**
```python
async def get_pages_to_fetch(self, notion_pages: List[Dict]) -> tuple:
    page_map = {...}

    # Batch query in chunks of 1000
    BATCH_SIZE = 1000
    page_ids = list(page_map.keys())
    existing_map = {}

    for i in range(0, len(page_ids), BATCH_SIZE):
        batch_ids = page_ids[i:i+BATCH_SIZE]
        response = await self.client.table("raw_notes").select(
            "notion_page_id, notion_last_edited_time"
        ).in_("notion_page_id", batch_ids).execute()

        for row in response.data:
            existing_map[row["notion_page_id"]] = row["notion_last_edited_time"]

    # ... rest of comparison logic
```

---

### 🚨 Issue 8: Database Index Performance
**Current Schema:**
```sql
CREATE INDEX idx_raw_notes_notion_page_id ON raw_notes(notion_page_id);
```

**Query Pattern:**
```sql
SELECT notion_page_id, notion_last_edited_time
FROM raw_notes
WHERE notion_page_id IN ('id1', 'id2', ..., 'id724');
```

**Performance:**
- 724 pages: < 50ms (fast)
- 10,000 pages: ~500ms (acceptable)
- Index is used efficiently

**Potential Issue:**
- If `notion_page_id` index is not UNIQUE, duplicates may exist
- Query returns multiple rows per page_id
- Comparison logic breaks

**Verification:**
```sql
-- Check for duplicates
SELECT notion_page_id, COUNT(*)
FROM raw_notes
GROUP BY notion_page_id
HAVING COUNT(*) > 1;
```

**If duplicates exist:**
```python
# Handle in query
existing_map = {}
for row in response.data:
    page_id = row["notion_page_id"]
    # Keep most recent
    if page_id not in existing_map:
        existing_map[page_id] = row["notion_last_edited_time"]
    else:
        existing_time = existing_map[page_id]
        new_time = row["notion_last_edited_time"]
        if new_time > existing_time:
            existing_map[page_id] = new_time
```

---

## Edge Cases & Error Handling

### Edge Case 1: Notion API Returns Stale last_edited_time
**Problem:** Notion API 캐시로 인해 수정했는데 타임스탬프 안 바뀜

**Solution:**
- Force fetch 옵션 추가 (query parameter)
- `?force=true` 시 변경 감지 무시하고 전체 fetch

**Implementation:**
```python
@router.post("/import-from-notion")
async def import_from_notion(
    page_size: int = Query(default=100, ...),
    force: bool = Query(default=False, description="Force fetch all pages (ignore change detection)"),
    ...
):
    # ...
    if force:
        logger.info("Force mode: skipping change detection")
        fetch_targets = set(page["id"] for page in pages)
    else:
        new_ids, updated_ids = await supabase_service.get_pages_to_fetch(pages)
        fetch_targets = set(new_ids + updated_ids)
```

---

### Edge Case 2: DB에는 있는데 Notion에서 삭제된 페이지
**Problem:** 페이지가 Notion에서 삭제되었는데 DB에 남아있음

**Solution:**
- Soft delete 구현 (is_deleted flag)
- 또는 삭제 감지 후 DB에서 제거

**Implementation (Optional):**
```python
# In _background_import_task()
all_notion_page_ids = set(page["id"] for page in pages)
existing_page_ids = set(existing_map.keys())
deleted_page_ids = existing_page_ids - all_notion_page_ids

if deleted_page_ids:
    logger.info(f"[Job {job_id}] Found {len(deleted_page_ids)} deleted pages")
    # Option A: Soft delete
    await supabase_service.client.table("raw_notes").update({
        "is_deleted": True
    }).in_("notion_page_id", list(deleted_page_ids)).execute()

    # Option B: Hard delete (not recommended)
    # await supabase_service.client.table("raw_notes").delete().in_("notion_page_id", list(deleted_page_ids)).execute()
```

**Schema Change (if soft delete):**
```sql
ALTER TABLE raw_notes ADD COLUMN is_deleted BOOLEAN DEFAULT FALSE;
CREATE INDEX idx_raw_notes_is_deleted ON raw_notes(is_deleted) WHERE is_deleted = FALSE;
```

---

### Edge Case 3: Timezone Handling
**Problem:** Notion uses UTC, Supabase may use different timezone

**Solution:**
- Always normalize to UTC with timezone-aware datetime
- Already implemented in `get_pages_to_fetch()`:
  ```python
  notion_time = datetime.fromisoformat(last_edited.replace("Z", "+00:00"))
  ```

---

### Edge Case 4: Race Condition (동시 Import)
**Problem:** 두 import job이 동시에 실행되어 중복 처리

**Solution:**
- Job locking mechanism (이미 구현됨 via import_jobs table)
- 또는 check if processing job exists:
  ```python
  # Before creating new job
  active_jobs = await supabase_service.client.table("import_jobs").select("id").eq("status", "processing").execute()
  if active_jobs.data:
      raise HTTPException(400, "Import already in progress")
  ```

---

## Migration Path

### Option 1: Direct Replacement (추천)
**Approach:** 기존 코드를 증분 업데이트 로직으로 대체

**Pros:**
- 코드 단순화
- 향후 유지보수 쉬움
- 모든 import가 자동으로 최적화

**Cons:**
- 초기 import 동작 변경 없음 (여전히 전체 fetch)

---

### Option 2: Feature Flag
**Approach:** 환경 변수로 증분/전체 모드 선택

```python
# config.py
INCREMENTAL_IMPORT: bool = Field(default=True, description="Enable incremental import")

# pipeline.py
if settings.incremental_import:
    new_ids, updated_ids = await supabase_service.get_pages_to_fetch(pages)
    fetch_targets = set(new_ids + updated_ids)
else:
    fetch_targets = set(page["id"] for page in pages)  # Fetch all
```

**Pros:**
- 안전한 롤백
- A/B 테스트 가능

**Cons:**
- 복잡도 증가
- 두 경로 모두 유지보수 필요

---

## Rollback Plan

**If issues occur:**

1. **Add force parameter:**
   ```bash
   curl -X POST "http://localhost:8000/pipeline/import-from-notion?force=true"
   ```

2. **Revert code:**
   ```bash
   git revert <commit-hash>
   ```

3. **Emergency fix:**
   ```python
   # Temporarily disable change detection
   fetch_targets = set(page["id"] for page in pages)  # Fetch all
   ```

---

## Files to Modify

| File | Type | Changes | Lines |
|------|------|---------|-------|
| `backend/services/supabase_service.py` | MODIFY | Add `get_pages_to_fetch()` method | +70 |
| `backend/routers/pipeline.py` | MODIFY | Update `_background_import_task()` | +15, ~5 modified |
| `backend/services/supabase_service.py` | MODIFY | Update `increment_job_progress()` | +3 |

**Total:** ~90 lines added/modified

---

## Verification Checklist

- [ ] Test Case 1: Initial import (724 pages, all fetched)
- [ ] Test Case 2: Re-import without changes (0 fetched, 724 skipped)
- [ ] Test Case 3: Single page added (1 fetched, 724 skipped)
- [ ] Test Case 4: 10 pages updated (10 fetched, 715 skipped)
- [ ] Test Case 5: Mixed changes (new + updated + unchanged)
- [ ] Performance: Import time reduced by >95% for incremental updates
- [ ] Logs: Show skip counts correctly
- [ ] Job status: `skipped_pages` field accurate
- [ ] Force mode: `?force=true` bypasses change detection
- [ ] Error handling: Invalid timestamps handled gracefully

---

## Timeline Estimate

| Phase | Time | Complexity |
|-------|------|------------|
| Phase 1: Add change detection | 30 min | Low |
| Phase 2: Update background task | 45 min | Medium |
| Phase 3: Update progress tracking | 15 min | Low |
| Phase 4: Testing (5 test cases) | 60 min | Medium |
| **Total** | **2.5 hours** | - |

---

## Expected Benefits

### Quantitative
- **API 호출 99% 절감** (평균 10개 변경 시)
- **Import 시간 98% 단축** (9분 → 3초)
- **DB 쓰기 98% 감소**
- **Rate limit 여유 증가** (다른 작업에 할당 가능)

### Qualitative
- **사용자 경험 개선**: 즉각적인 동기화
- **비용 절감**: API 호출 횟수 감소
- **확장성**: 페이지 수 증가해도 성능 유지
- **안정성**: 불필요한 API 호출 감소로 rate limit 에러 방지

---

## Future Enhancements

### 1. Webhook-based Real-time Sync
**Concept:** Notion webhook → 즉시 import (polling 불필요)

```python
@router.post("/webhook/notion")
async def notion_webhook(payload: Dict):
    page_id = payload["page_id"]
    # Trigger import for single page
    background_tasks.add_task(import_single_page, page_id)
```

---

### 2. Batch Change Detection
**Concept:** 변경 페이지를 먼저 그룹화하여 batch API 호출

```python
# Instead of:
for page_id in fetch_targets:
    content = await fetch_page_blocks(page_id)  # N calls

# Use batch API (if available):
contents = await fetch_page_blocks_batch(fetch_targets)  # 1 call
```

---

### 3. Cache Layer (Redis)
**Concept:** last_edited_time을 Redis에 캐싱하여 DB 쿼리 생략

```python
# Check Redis first
cached_times = await redis.mget([f"page:{id}" for id in page_ids])
# Only query DB for cache misses
```

---

## Conclusion

이 증분 업데이트 방식은:
- ✅ **효율성**: API 호출 99% 절감
- ✅ **확장성**: 페이지 수 증가해도 성능 유지
- ✅ **단순성**: 90줄 추가로 구현 가능
- ✅ **안정성**: 기존 로직 유지, force 옵션으로 fallback
- ✅ **호환성**: 기존 API 변경 없음 (backward compatible)

**추천:** 즉시 구현 진행.

---

## 📊 현재 API 호출 로직 구조도 (Current Implementation)

### 전체 흐름도 (Overall Flow)

```
[Client Request]
    ↓
POST /pipeline/import-from-notion?page_size=100
    ↓
[FastAPI Router: pipeline.py]
    ↓
    ├─ Create import job (DB)
    ├─ Launch background task
    └─ Return job_id immediately (202 Accepted)

[Background Task: _background_import_task()]
    ↓
    ├─ Mark job as "processing"
    ├─ Determine mode (database vs parent_page)
    └─ Fetch metadata
        ↓
        ┌──────────────────────────────────────┐
        │ 1. Notion API: blocks.children.list  │
        │    (Metadata만 가져오기 - lightweight)│
        │    Input: parent_page_id             │
        │    Output: 726 child_page blocks     │
        │    Time: ~2-3 minutes (rate limited) │
        └──────────────────────────────────────┘
                ↓
        ┌──────────────────────────────────────┐
        │ 2. Change Detection (NEW)            │
        │    get_pages_to_fetch()              │
        │    - Query DB for existing pages     │
        │    - Compare last_edited_time        │
        │    - Return (new_ids, updated_ids)   │
        │    Time: <1 second                   │
        └──────────────────────────────────────┘
                ↓
        ┌──────────────────────────────────────┐
        │ 3. Content Fetching Loop             │
        │    FOR each page in pages:           │
        │      IF page_id in fetch_targets:    │
        │        - Notion API: fetch_page_blocks│
        │        - Upsert to DB                │
        │      ELSE:                           │
        │        - Skip (log + counter)        │
        │    Time: depends on changed pages    │
        └──────────────────────────────────────┘
                ↓
        ┌──────────────────────────────────────┐
        │ 4. Update job status                 │
        │    - imported_pages                  │
        │    - skipped_pages                   │
        │    - failed_pages                    │
        │    - Mark job as "completed"         │
        └──────────────────────────────────────┘
```

---

### 상세 API 호출 시퀀스 (Detailed API Call Sequence)

#### 🔵 Phase 1: Metadata Fetch (Lightweight)

```
[notion_service.fetch_child_pages_from_parent()]
    ↓
    WHILE has_more:
        ↓
        await rate_limiter.acquire()  # 3 req/sec limit
        ↓
        ┌─────────────────────────────────────────┐
        │ Notion API: blocks.children.list        │
        │ ───────────────────────────────────────│
        │ URL: POST /v1/blocks/{parent_id}/children│
        │ Params:                                 │
        │   - page_size: 100                      │
        │   - start_cursor: (for pagination)      │
        │                                         │
        │ Response per batch:                     │
        │   {                                     │
        │     "results": [                        │
        │       {                                 │
        │         "type": "child_page",           │
        │         "id": "abc-123-...",           │
        │         "created_time": "2024-01-01...",│
        │         "last_edited_time": "2024-01-15│
        │         "child_page": {"title": "..."}  │
        │       },                                │
        │       ...                               │
        │     ],                                  │
        │     "has_more": true,                   │
        │     "next_cursor": "..."                │
        │   }                                     │
        │                                         │
        │ ⚠️  주의: last_edited_time은 BLOCK의    │
        │          수정 시간 (content 변경 반영?)  │
        └─────────────────────────────────────────┘
        ↓
        Extract child_page blocks only
        Build page_data objects:
          {
            "id": "page-id",
            "url": "https://notion.so/...",
            "created_time": "...",
            "last_edited_time": "...",  # 🔑 Key for comparison
            "properties": {"제목": "..."}
          }
        ↓
        Append to all_child_pages[]
        ↓
        [NEXT BATCH if has_more=true]

    RETURN all_child_pages  # 726개 page objects
```

**API 호출 횟수:**
- Batches: 726 pages ÷ 100 per batch = 8 batches
- Calls: 8 API calls
- Time: 8 calls ÷ 3 req/sec = ~2.7 seconds

---

#### 🟢 Phase 2: Change Detection (NEW - Database Query)

```
[supabase_service.get_pages_to_fetch(pages)]
    ↓
    # Step 1: Parse Notion timestamps
    page_map = {}
    FOR each page in pages:
        notion_time = parse(page["last_edited_time"])
        notion_time = notion_time.replace(microsecond=0)  # Truncate
        page_map[page["id"]] = notion_time

    # Step 2: Query DB in batches
    ┌─────────────────────────────────────────┐
    │ Supabase Query: raw_notes table        │
    │ ───────────────────────────────────────│
    │ URL: GET /rest/v1/raw_notes            │
    │ Params:                                 │
    │   ?select=notion_page_id,notion_last_edited_time│
    │   &notion_page_id=in.(id1,id2,...,id1000)│  ❌ URL TOO LONG
    │                                         │
    │ BATCH_SIZE: 1000 (currently)            │
    │ With 726 pages:                         │
    │   - Batch 1: 726 IDs in URL             │
    │   - URL length: ~27KB                   │
    │   - Result: 400 Bad Request ❌          │
    └─────────────────────────────────────────┘
    ↓
    ❌ ERROR: URL length exceeds limit
    ↓
    FALLBACK: return (all_page_ids, [])  # Treat all as "new"
    ↓
    RESULT: 726 pages marked as "new"
    NO PAGES SKIPPED ❌
```

**🚨 BUG LOCATION: Line 932 in supabase_service.py**
```python
.in_("notion_page_id", batch_ids)  # HTTP GET with 726 UUIDs in URL
# URL: ?notion_page_id=in.(uuid1,uuid2,...,uuid726)
# Length: 36 chars × 726 + separators = ~27,000 chars
# HTTP GET limit: ~8,192 chars (most servers)
# Result: 400 Bad Request
```

---

#### 🟡 Phase 3: Content Fetching Loop (Selective - Should Work But Doesn't Due To Bug)

```
[_background_import_task() - Loop]
    ↓
    fetch_targets = set(new_page_ids + updated_page_ids)
    # Currently: fetch_targets = all 726 pages (due to bug)
    ↓
    FOR idx, page in enumerate(pages):  # 726 iterations
        page_id = page["id"]
        ↓
        ┌─────────────────────────────────────────┐
        │ Conditional Check                       │
        │ ───────────────────────────────────────│
        │ IF page_id NOT IN fetch_targets:        │
        │   → Skip (log + increment skipped)      │
        │   → Time: ~0.001 sec                    │
        │                                         │
        │ ELSE:  (page_id IN fetch_targets)       │
        │   ↓                                     │
        │   ┌─────────────────────────────────┐   │
        │   │ Notion API: blocks.children.list│   │
        │   │ ─────────────────────────────── │   │
        │   │ URL: GET /v1/blocks/{page_id}/  │   │
        │   │       children                  │   │
        │   │                                 │   │
        │   │ Extracts:                       │   │
        │   │ - Paragraphs                    │   │
        │   │ - Headings                      │   │
        │   │ - Lists                         │   │
        │   │ - Quotes                        │   │
        │   │ - Code blocks                   │   │
        │   │ - etc.                          │   │
        │   │                                 │   │
        │   │ Rate limit: 3 req/sec           │   │
        │   │ Time per call: ~0.33 sec        │   │
        │   └─────────────────────────────────┘   │
        │   ↓                                     │
        │   Upsert to DB (raw_notes)              │
        │   ↓                                     │
        │   Increment imported_pages              │
        └─────────────────────────────────────────┘
```

**예상 성능 (버그 수정 후):**

| Scenario | Fetch Targets | API Calls | Time |
|----------|---------------|-----------|------|
| 초기 import | 726 (all new) | 726 | ~4 min |
| 1개 변경 | 1 | 1 | <1 sec |
| 10개 변경 | 10 | 10 | ~3 sec |
| 변경 없음 | 0 | 0 | <1 sec |

**현재 실제 성능 (버그로 인해):**

| Scenario | Fetch Targets | API Calls | Time |
|----------|---------------|-----------|------|
| 모든 경우 | 726 (all "new") | 726 | ~4 min |

---

### 🔴 핵심 버그 분석 (Root Cause Analysis)

#### 버그 위치: `supabase_service.py:932`

```python
# Line 927-934
for i in range(0, len(page_ids), BATCH_SIZE):  # BATCH_SIZE = 1000
    batch_ids = page_ids[i:i+BATCH_SIZE]  # First batch: all 726 IDs
    response = await (
        self.client.table("raw_notes")
        .select("notion_page_id, notion_last_edited_time")
        .in_("notion_page_id", batch_ids)  # ❌ HTTP GET with long URL
        .execute()
    )
```

**HTTP Request 생성:**
```http
GET /rest/v1/raw_notes?select=notion_page_id%2Cnotion_last_edited_time&notion_page_id=in.%28556603bc-bad1-4f3a-af19-64619edbe24c%2C255b94e5-8350-49a3-a03f-57fa98bde45c%2C84f78b4c-3d22-42fa-99f6-f34beb84452d%2C...%2C2e92686c-2113-81df-b45e-dbea55faa2dc%29 HTTP/2
Host: zqrbrddmwrpogabizton.supabase.co
```

**URL 길이 계산:**
```
Base URL + params: ~200 chars
Each UUID: 36 chars
Separators (,): 725 × 1 = 725 chars
Total UUIDs: 726 × 36 = 26,136 chars
─────────────────────────────────
Total: ~27,061 chars
```

**HTTP 서버 제한:**
- RFC 7230: No hard limit (구현체 의존)
- 실제 구현:
  - Nginx: 4KB-8KB (default)
  - Apache: 8KB (default)
  - Browsers: 2KB (Chrome, Firefox)
  - Supabase/PostgREST: Unknown but < 27KB

**결과:**
```
HTTP/2 400 Bad Request
{
  "message": "JSON could not be generated",
  "code": 400,
  "details": "Bad Request"
}
```

**Fallback 로직 작동:**
```python
# Line 954-957
except Exception as e:
    logger.error(f"Failed to fetch existing pages: {e}")
    # On error, treat all as new (safe fallback)
    return list(page_map.keys()), []  # 🔥 All 726 pages as "new"
```

---

### 🔄 성능 비교: 전체 조회 vs 배치 조회

#### 방식 A: 전체 테이블 조회 (Full Table Scan)
```python
# DB에서 모든 페이지 가져오기 (필터 없음)
response = await self.client.table("raw_notes").select(
    "notion_page_id, notion_last_edited_time"
).execute()

# Python에서 필터링
existing_map = {row["notion_page_id"]: row["notion_last_edited_time"]
                for row in response.data}
```

**비용 분석:**
- DB Query: 1회 (전체 스캔)
- 네트워크 전송: 732 rows × ~60 bytes = ~44KB
- DB 처리 시간: ~50-100ms (인덱스 무시, 전체 스캔)
- 네트워크 시간: ~50ms
- Python 처리: ~10ms (딕셔너리 생성)
- **총 시간: ~110-160ms**

**장점:**
- 단순한 로직
- URL 길이 제한 없음
- DB 쿼리 1회

**단점:**
- 불필요한 데이터 전송 (현재 import와 무관한 페이지도 가져옴)
- 페이지 수가 많을수록 비효율 증가
- 메모리 사용량 증가

---

#### 방식 B: 배치 필터링 조회 (Batched Filtered Query)
```python
# BATCH_SIZE = 100
# 8 batches for 726 pages
for i in range(0, len(page_ids), BATCH_SIZE):
    batch_ids = page_ids[i:i+BATCH_SIZE]  # 100 IDs
    response = await self.client.table("raw_notes").select(
        "notion_page_id, notion_last_edited_time"
    ).in_("notion_page_id", batch_ids).execute()

    # Accumulate results
```

**비용 분석:**
- DB Query: 8회 (인덱스 사용)
- 네트워크 전송: 726 rows × ~60 bytes = ~44KB (동일)
- DB 처리 시간: 8 × 10ms = 80ms (인덱스 lookup)
- 네트워크 시간: 8 × 50ms = 400ms (왕복 지연)
- Python 처리: ~10ms
- **총 시간: ~490ms**

**장점:**
- 필요한 페이지만 조회 (Notion API에서 가져온 726개만)
- 인덱스 활용 (idx_raw_notes_notion_page_id)
- 확장성 좋음 (DB에 10만개 페이지 있어도 속도 동일)

**단점:**
- 네트워크 왕복 8회
- 로직 복잡도 증가
- URL 길이 제한 주의 필요

---

### 📊 시나리오별 성능 비교

#### 시나리오 1: 현재 상황 (DB 732페이지, Import 726페이지)

| 항목 | 방식 A (전체 조회) | 방식 B (배치 조회) | 승자 |
|------|-------------------|-------------------|------|
| DB 쿼리 수 | 1 | 8 | A |
| DB 처리 시간 | 50-100ms | 80ms | B |
| 네트워크 왕복 | 1 | 8 | A |
| 네트워크 시간 | 50ms | 400ms | A |
| 총 시간 | **~110-160ms** | **~490ms** | **A 승리** |
| 데이터 전송량 | 44KB | 44KB | 동일 |

**결론: 방식 A가 **3배 빠름**

---

#### 시나리오 2: 대규모 DB (DB 10,000페이지, Import 726페이지)

| 항목 | 방식 A (전체 조회) | 방식 B (배치 조회) | 승자 |
|------|-------------------|-------------------|------|
| DB 쿼리 수 | 1 | 8 | A |
| DB 처리 시간 | 200-300ms | 80ms | B |
| 네트워크 전송 | 10,000 × 60 = 600KB | 726 × 60 = 44KB | **B (13배 적음)** |
| 네트워크 시간 | 300ms | 400ms | A |
| 총 시간 | **~500-600ms** | **~490ms** | **B 승리** |

**결론: 방식 B가 약간 빠름, 데이터 전송량 **13배 적음**

---

#### 시나리오 3: 소규모 Import (DB 732페이지, Import 10페이지)

| 항목 | 방식 A (전체 조회) | 방식 B (배치 조회) | 승자 |
|------|-------------------|-------------------|------|
| DB 쿼리 수 | 1 | 1 | 동일 |
| DB 처리 시간 | 50-100ms | 10ms | B |
| 네트워크 전송 | 732 × 60 = 44KB | 10 × 60 = 0.6KB | **B (73배 적음)** |
| 네트워크 시간 | 50ms | 50ms | 동일 |
| 총 시간 | **~110-160ms** | **~70ms** | **B 승리** |

**결론: 방식 B가 **2배 빠름**, 데이터 전송량 **73배 적음**

---

### 🎯 최종 결론 및 권장사항

#### 현재 상황 (DB 732, Import 726)
- **권장: 방식 A (전체 조회)**
- 이유: 3배 빠름 (110ms vs 490ms)
- DB와 Import 크기가 거의 동일하므로 불필요한 데이터 거의 없음

#### 미래 확장성 고려
- **권장: 방식 B (배치 조회)**
- 이유:
  1. DB가 10,000개로 증가 시 성능 유지
  2. 소규모 import 시 훨씬 효율적
  3. 네트워크 대역폭 절약

---

### 🏆 추천 구현: 하이브리드 방식

```python
async def get_pages_to_fetch(
    self,
    notion_pages: List[Dict[str, Any]]
) -> tuple[List[str], List[str]]:
    """
    Smart change detection with adaptive strategy.
    """
    await self._ensure_initialized()

    page_map = {...}  # Parse Notion pages

    # Get total DB count
    count_response = await self.client.table("raw_notes").select(
        "notion_page_id", count="exact"
    ).execute()
    total_db_pages = count_response.count

    # Adaptive strategy
    if len(page_map) >= total_db_pages * 0.8:
        # Import covers >80% of DB → Full scan cheaper
        logger.info(f"Using full scan strategy ({len(page_map)}/{total_db_pages} pages)")
        response = await self.client.table("raw_notes").select(
            "notion_page_id, notion_last_edited_time"
        ).execute()
        existing_map = {row["notion_page_id"]: row["notion_last_edited_time"]
                       for row in response.data}
    else:
        # Import is subset → Batched query more efficient
        logger.info(f"Using batched query strategy ({len(page_map)}/{total_db_pages} pages)")
        BATCH_SIZE = 100
        existing_map = {}
        for i in range(0, len(page_ids), BATCH_SIZE):
            batch_ids = page_ids[i:i+BATCH_SIZE]
            response = await self.client.table("raw_notes").select(
                "notion_page_id, notion_last_edited_time"
            ).in_("notion_page_id", batch_ids).execute()
            for row in response.data:
                existing_map[row["notion_page_id"]] = row["notion_last_edited_time"]

    # Compare and return
    # ...
```

**로직:**
- Import가 DB의 80% 이상 커버 → 전체 조회 (현재: 726/732 = 99%)
- Import가 DB의 80% 미만 → 배치 조회
- 미래에 DB 10,000개, Import 100개 → 자동으로 배치 조회 사용

**장점:**
- 현재: 최적 성능 (110ms)
- 미래: 확장성 보장
- 자동 최적화 (코드 변경 없음)

---

### 해결책 (Solutions)

#### ✅ Solution 1: Reduce BATCH_SIZE (Immediate Fix)

```python
# Line 922
BATCH_SIZE = 100  # Was: 1000

# URL length with 100 UUIDs:
# 36 × 100 + 99 + 200 = ~3,800 chars ✅ Safe
```

**적용 후 성능:**
- Batches: 726 ÷ 100 = 8 batches
- DB queries: 8
- Query time: 8 × 50ms = 400ms
- Total: <1 second ✅

#### ✅ Solution 2: Supabase RPC with Custom Function (Better)

**개념:** Supabase에 커스텀 PostgreSQL 함수를 만들고, Python에서 RPC로 호출

**장점:**
- HTTP POST 사용 (URL 길이 제한 없음)
- 배열 파라미터로 전달 (JSON body에 포함)
- 서버에서 처리 (네트워크 왕복 1회)

**Step 1: Supabase에 SQL 함수 생성**
```sql
CREATE OR REPLACE FUNCTION get_pages_by_ids(page_ids text[])
RETURNS TABLE(notion_page_id text, notion_last_edited_time timestamptz)
AS $$
BEGIN
    RETURN QUERY
    SELECT rn.notion_page_id, rn.notion_last_edited_time
    FROM raw_notes rn
    WHERE rn.notion_page_id = ANY(page_ids);  -- 배열로 필터링
END;
$$ LANGUAGE plpgsql;
```

**Step 2: Python에서 RPC 호출**
```python
# POST 요청으로 전환 (URL에 데이터 없음)
response = await self.client.rpc('get_pages_by_ids', {
    'page_ids': batch_ids  # JSON body에 포함
}).execute()

# HTTP Request 예시:
# POST /rest/v1/rpc/get_pages_by_ids HTTP/2
# Content-Type: application/json
# Body: {"page_ids": ["uuid1", "uuid2", ..., "uuid726"]}
```

**비용 분석:**
- DB Query: 1회 (서버에서 실행)
- 네트워크 전송: 726 rows × ~60 bytes = ~44KB
- HTTP Method: **POST** (body에 데이터, URL 짧음)
- DB 처리 시간: ~80ms (인덱스 사용)
- 네트워크 시간: ~50ms (1회 왕복)
- **총 시간: ~130ms**

**방식 A vs 방식 B (RPC):**

| 항목 | 방식 A (전체 조회) | 방식 B (RPC 배치) | 승자 |
|------|-------------------|------------------|------|
| DB 쿼리 | 1 | 1 | 동일 |
| 네트워크 왕복 | 1 | 1 | 동일 |
| 총 시간 | 110ms | **130ms** | A |
| URL 길이 제한 | 없음 | 없음 (POST) | 동일 |
| 확장성 | DB 증가 시 느려짐 | DB 증가해도 동일 | **B** |

**결론:**
- 현재: 방식 A가 20ms 빠름 (무시할 정도)
- 미래: DB 10,000개 시 방식 B가 훨씬 효율적

---

#### ✅ Solution 3: Server-Side Comparison (Best - All Logic on Server)

**개념:** 비교 로직까지 DB에서 처리 (Python은 결과만 받음)

**장점:**
- 네트워크 전송 최소화 (결과만 전송)
- DB에서 timestamp 비교 (더 빠름)
- Python 처리 불필요

**Step 1: Supabase에 비교 함수 생성**
```sql
CREATE OR REPLACE FUNCTION get_changed_pages(pages_data jsonb)
RETURNS jsonb
AS $$
DECLARE
    result jsonb;
    new_ids text[];
    updated_ids text[];
    page_record jsonb;
    notion_id text;
    notion_time timestamptz;
    db_time timestamptz;
BEGIN
    new_ids := ARRAY[]::text[];
    updated_ids := ARRAY[]::text[];

    -- Notion에서 가져온 각 페이지 처리
    FOR page_record IN SELECT * FROM jsonb_array_elements(pages_data)
    LOOP
        notion_id := page_record->>'id';
        notion_time := (page_record->>'last_edited')::timestamptz;

        -- DB에서 해당 페이지 조회
        SELECT notion_last_edited_time INTO db_time
        FROM raw_notes
        WHERE notion_page_id = notion_id;

        IF NOT FOUND THEN
            -- 신규 페이지
            new_ids := array_append(new_ids, notion_id);
        ELSIF notion_time > db_time THEN
            -- 수정된 페이지
            updated_ids := array_append(updated_ids, notion_id);
        END IF;
        -- ELSE: unchanged (skip)
    END LOOP;

    -- 결과 반환
    result := jsonb_build_object(
        'new_page_ids', to_jsonb(new_ids),
        'updated_page_ids', to_jsonb(updated_ids)
    );

    RETURN result;
END;
$$ LANGUAGE plpgsql;
```

**Step 2: Python에서 RPC 호출**
```python
# Notion 페이지를 JSON으로 변환
pages_json = [
    {
        "id": page["id"],
        "last_edited": page["last_edited_time"]
    }
    for page in notion_pages
]

# RPC 호출 (비교 로직 전체를 DB에서 처리)
response = await self.client.rpc('get_changed_pages', {
    'pages_data': json.dumps(pages_json)
}).execute()

# 결과만 받음 (이미 분류된 상태)
result = response.data
new_page_ids = result['new_page_ids']      # ["uuid1", "uuid2", ...]
updated_page_ids = result['updated_page_ids']  # ["uuid3", "uuid4", ...]

# HTTP Request 예시:
# POST /rest/v1/rpc/get_changed_pages HTTP/2
# Body: {
#   "pages_data": [
#     {"id": "uuid1", "last_edited": "2024-01-15T14:30:00Z"},
#     {"id": "uuid2", "last_edited": "2024-01-15T15:00:00Z"},
#     ...
#   ]
# }
#
# Response:
# {
#   "new_page_ids": ["uuid1", "uuid10", ...],
#   "updated_page_ids": ["uuid5", "uuid20", ...]
# }
```

**비용 분석:**
- DB Query: 726회 (각 페이지마다 1회 lookup, 하지만 서버 내부라 빠름)
- 네트워크 전송 (요청): 726 × ~80 bytes = ~58KB (id + timestamp)
- 네트워크 전송 (응답): 변경된 페이지 ID만 (예: 10개 × 36 bytes = 360 bytes)
- DB 처리 시간: ~100ms (루프 + 인덱스 lookup)
- 네트워크 시간: ~50ms (1회 왕복)
- Python 처리: **0ms** (비교 로직 없음)
- **총 시간: ~150ms**

**비교: 방식 A vs RPC 배치 vs RPC 비교**

| 항목 | 방식 A (전체) | Solution 2 (RPC 배치) | Solution 3 (RPC 비교) | 승자 |
|------|--------------|---------------------|---------------------|------|
| 네트워크 왕복 | 1 | 1 | 1 | 동일 |
| 네트워크 전송 (요청) | 최소 | 58KB | 58KB | A |
| 네트워크 전송 (응답) | 44KB | 44KB | **0.4KB** (10개만) | **C** |
| DB 쿼리 | 1 (full scan) | 1 (filtered) | 726 (indexed) | B |
| Python 처리 | 비교 필요 | 비교 필요 | **불필요** | **C** |
| 총 시간 | 110ms | 130ms | **150ms** | A |
| 확장성 | 나쁨 | **최고** | 좋음 | **B** |

**시나리오별 승자:**

| 시나리오 | 방식 A | Solution 2 | Solution 3 | 최적 |
|---------|-------|-----------|-----------|------|
| 현재 (732/726) | **110ms** | 130ms | 150ms | **A** |
| DB 10,000 (10%) | 500ms | **130ms** | **150ms** | **Sol 2/3** |
| 변경 1개만 | 110ms | 130ms | **80ms** (응답 작음) | **Sol 3** |

**Solution 3의 진가:**
- **변경이 적을수록** 더 효율적 (응답 크기 최소)
- DB 10,000개, 변경 10개: 응답 360 bytes vs 44KB (122배 적음)
- 네트워크 대역폭 절약 (모바일 등에 유리)

---

### ⚠️ 핵심 질문: "Solution 3도 UUID 전부 보내는데 기존과 뭐가 달라?"

**정답: HTTP Method의 차이!**

#### 기존 방식 (배치 조회 - 버그 발생)
```python
# HTTP GET 방식
response = await self.client.table("raw_notes").select(
    "notion_page_id, notion_last_edited_time"
).in_("notion_page_id", batch_ids).execute()

# 생성되는 HTTP Request:
# GET /rest/v1/raw_notes?notion_page_id=in.(uuid1,uuid2,...,uuid726)
#     ^^^                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#     GET                      URL에 데이터 포함 (27KB)
#
# ❌ 문제: URL 길이 제한 초과 (8KB 제한)
```

#### Solution 2 & 3 (RPC - POST 방식)
```python
# HTTP POST 방식 (RPC)
response = await self.client.rpc('get_pages_by_ids', {
    'page_ids': batch_ids  # JSON body에 포함
}).execute()

# 생성되는 HTTP Request:
# POST /rest/v1/rpc/get_pages_by_ids
#      ^^^
#      POST
# Content-Type: application/json
# Body: {"page_ids": ["uuid1", "uuid2", ..., "uuid726"]}
#       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#       Body에 데이터 포함 (크기 제한 없음)
#
# ✅ 해결: Body는 수십 MB도 가능
```

---

### 📊 핵심 차이점 정리

| 항목 | 기존 (GET + .in_) | Solution 2/3 (POST + RPC) |
|------|------------------|---------------------------|
| **HTTP Method** | **GET** | **POST** |
| **데이터 위치** | **URL 쿼리스트링** | **Request Body** |
| **크기 제한** | **~8KB (URL 제한)** | **수십 MB (Body 제한)** |
| **726개 UUID** | ❌ 불가능 (27KB) | ✅ 가능 (58KB body) |
| **Supabase SDK 지원** | `.in_()` (GET만 지원) | `.rpc()` (POST 지원) |

---

### 🤔 그럼 왜 방식 A (전체 조회)는 문제없었나?

```python
# 방식 A: 전체 조회
response = await self.client.table("raw_notes").select(
    "notion_page_id, notion_last_edited_time"
).execute()

# HTTP Request:
# GET /rest/v1/raw_notes?select=notion_page_id,notion_last_edited_time
#                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#                        URL에 필터 조건 없음! (짧은 URL)
```

**필터 없이 전체 조회 → URL 짧음 → 문제 없음!**

---

### 💡 결론

**버그의 원인:**
- Supabase Python SDK의 `.in_()` 메서드는 **HTTP GET**을 사용
- 726개 UUID를 **URL 쿼리스트링**에 넣음 (27KB)
- HTTP GET URL 제한 초과 (8KB)

**Solution 2/3의 핵심:**
- `.rpc()` 메서드는 **HTTP POST**를 사용
- 726개 UUID를 **JSON Body**에 넣음 (제한 없음)
- 같은 양의 데이터를 보내지만 **전송 방식이 다름**

**비유:**
- GET: 엽서에 주소를 적음 (글자 수 제한 있음)
- POST: 택배 상자에 물건을 넣음 (무게 제한 훨씬 큼)

---

### 🎯 최종 정리: 왜 여러 솔루션이 있나?

| Solution | HTTP | 데이터 전송 | 장점 | 현재 상황 추천 |
|----------|------|-----------|------|--------------|
| **방식 A** | GET | 없음 (전체 조회) | **가장 빠름** (110ms) | ✅ **1순위** |
| **Solution 1** | GET | URL (100개씩) | 기존 코드 수정 최소 | 차선책 |
| **Solution 2** | POST | Body (726개) | 확장성 좋음 | 미래 고려 시 |
| **Solution 3** | POST | Body (726개) | 응답 최소화 | 변경 적을 때 최적 |

**핵심 차이:**
- **GET (URL)**: 작은 데이터만 (< 8KB)
- **POST (Body)**: 큰 데이터 가능 (> 수십 MB)
- **방식 A**: 아예 필터 안 씀 (전체 조회)

---

### 🤔 추가 질문: "그럼 처음부터 Solution 3으로 만들면?"

**답변: 맞습니다! 장기적으로는 Solution 3이 최적입니다.**

#### 시나리오별 성능 예측

| DB 크기 | Import 크기 | 변경 비율 | 방식 A | Solution 3 | 승자 |
|---------|-----------|----------|--------|-----------|------|
| 732 | 726 | 99% | **110ms** | 150ms | A |
| 1,000 | 1,000 | 100% | 150ms | 170ms | A |
| 10,000 | 726 | 7% | 500ms | **150ms** | **Sol 3** |
| 10,000 | 100 | 1% | 500ms | **120ms** | **Sol 3** |
| 100,000 | 1,000 | 1% | 5,000ms | **200ms** | **Sol 3** |

**결론: DB가 커질수록 Solution 3이 압도적으로 빠름!**

---

#### 💡 왜 지금 당장 Solution 3을 추천하지 않나?

**1. 추가 인프라 필요**
```sql
-- Supabase에 커스텀 함수 배포 필요
CREATE OR REPLACE FUNCTION get_changed_pages(pages_data jsonb)
RETURNS jsonb AS $$ ... $$;
```
- 운영 복잡도 증가
- Supabase 마이그레이션 스크립트 관리 필요
- 함수 버그 시 디버깅 어려움 (DB 로그 확인 필요)

**2. 현재는 성능 차이 미미**
- 방식 A: 110ms
- Solution 3: 150ms
- 차이: 40ms (0.04초 - 사용자가 느끼지 못함)

**3. 코드 단순성**
```python
# 방식 A: 3줄
response = await self.client.table("raw_notes").select(
    "notion_page_id, notion_last_edited_time"
).execute()

# Solution 3: SQL 함수 + Python 코드 + 에러 핸들링
# → 총 ~100줄
```

---

#### 🎯 최종 권장 전략

**Phase 1 (즉시): 방식 A**
- 코드 단순
- 인프라 변경 없음
- 충분히 빠름 (110ms)

**Phase 2 (DB 5,000개 도달 시): Solution 3으로 마이그레이션**
- SQL 함수 배포
- Python 코드 교체
- 성능 극대화

**트리거 조건:**
```python
# config.py
USE_RPC_CHANGE_DETECTION = os.getenv("USE_RPC_CHANGE_DETECTION", "false").lower() == "true"

# supabase_service.py
if settings.use_rpc_change_detection:
    # Solution 3: RPC 호출
    return await self._get_pages_to_fetch_rpc(notion_pages)
else:
    # 방식 A: 전체 조회
    return await self._get_pages_to_fetch_full_scan(notion_pages)
```

**마이그레이션 시점:**
- DB 페이지 5,000개 초과 시
- 또는 성능 이슈 발생 시
- 환경변수로 간단히 전환

---

### 🔍 질문 2: "Supabase Python SDK는 자동으로 POST 사용하지 않음"의 의미

**배경: Supabase SDK의 HTTP Method 선택 로직**

#### Supabase Python SDK의 내부 동작

```python
# supabase-py 내부 코드 (simplified)
class PostgrestClient:
    def select(self, columns):
        # SELECT는 항상 GET 사용
        return self._request("GET", "/table_name", params={"select": columns})

    def in_(self, column, values):
        # IN 조건도 GET의 쿼리스트링에 추가
        # URL: GET /table?column=in.(value1,value2,...)
        self.params[column] = f"in.({','.join(values)})"
        return self

    def rpc(self, function_name, params):
        # RPC는 항상 POST 사용
        return self._request("POST", f"/rpc/{function_name}", json=params)
```

#### 핵심: SDK가 자동으로 판단하지 않음

**문제 상황:**
```python
# 개발자가 원하는 것: "데이터 많으면 자동으로 POST 쓰면 좋겠다"
batch_ids = ["uuid1", "uuid2", ..., "uuid726"]  # 많은 데이터

response = await client.table("raw_notes").select("*").in_("id", batch_ids).execute()
# ❌ SDK는 무조건 GET 사용 (데이터 크기 신경 안 씀)
# ❌ URL: GET /raw_notes?id=in.(uuid1,uuid2,...,uuid726) → 27KB URL
```

**SDK가 하지 않는 것:**
```python
# ✗ 이런 로직이 없음:
if len(batch_ids) > 100:  # 데이터가 많으면
    use_post_request()    # 자동으로 POST로 전환
else:
    use_get_request()     # 적으면 GET 사용
```

#### 해결: 개발자가 명시적으로 POST 사용

```python
# 방법 1: RPC 사용 (POST 강제)
response = await client.rpc('get_pages_by_ids', {
    'page_ids': batch_ids  # ✅ 자동으로 POST body에 포함
}).execute()

# 방법 2: 직접 HTTP POST 요청 (low-level)
import httpx
response = await httpx.post(
    f"{SUPABASE_URL}/rest/v1/raw_notes",
    json={"id": {"in": batch_ids}},  # ✅ Body에 포함
    headers={"apikey": SUPABASE_KEY}
)
```

#### 왜 SDK가 자동 전환하지 않나?

**1. RESTful 규약**
- GET: 읽기 (멱등성, 캐시 가능)
- POST: 쓰기/RPC (부작용 가능)
- SELECT 쿼리는 의미상 GET이 맞음

**2. 하위 호환성**
- 기존 코드가 GET으로 작동
- 갑자기 POST로 바뀌면 캐싱/로깅 등에 영향

**3. 명시성**
- 개발자가 명시적으로 선택하게 함
- "큰 데이터는 RPC 쓰세요"가 설계 의도

---

### 📊 정리

| 질문 | 답변 |
|------|------|
| **처음부터 Solution 3?** | 장기적으로 맞지만, 현재는 오버엔지니어링. DB 5,000개 넘으면 전환 추천. |
| **SDK POST 자동 전환?** | 안 함. `.in_()`은 항상 GET. `.rpc()`만 POST. 개발자가 명시적으로 선택해야 함. |
| **최선책?** | Phase 1: 방식 A로 시작 → Phase 2: DB 커지면 Solution 3으로 마이그레이션 |

---

## 🎯 Final Recommendation

### Immediate Action: Implement 방식 A (Full Table Scan)

**Why:**
1. **Simplest solution** - 3 lines of code change
2. **Fastest for current scale** - 110ms vs 490ms (batched) or 150ms (RPC)
3. **No infrastructure changes** - No SQL functions to deploy
4. **Zero risk** - Proven pattern, no URL length issues
5. **Sufficient performance** - 110ms is imperceptible to users

**Implementation:**
```python
# backend/services/supabase_service.py (MODIFY)
async def get_pages_to_fetch(
    self,
    notion_pages: List[Dict[str, Any]]
) -> tuple[List[str], List[str]]:
    """Compare Notion pages with DB to detect changes."""
    await self._ensure_initialized()

    # Parse Notion timestamps
    page_map = {}
    for p in notion_pages:
        page_id = p.get("id")
        last_edited = p.get("last_edited_time")
        if not page_id or not last_edited:
            continue
        notion_time = datetime.fromisoformat(last_edited.replace("Z", "+00:00"))
        notion_time = notion_time.replace(microsecond=0)  # Truncate
        page_map[page_id] = notion_time

    if not page_map:
        return [], []

    # 방식 A: Full table scan (SIMPLE & FAST)
    try:
        response = await (
            self.client.table("raw_notes")
            .select("notion_page_id, notion_last_edited_time")
            .execute()
        )

        existing_map = {}
        for row in response.data:
            db_time = row["notion_last_edited_time"]
            if isinstance(db_time, str):
                db_time = datetime.fromisoformat(db_time.replace("Z", "+00:00"))
            db_time = db_time.replace(microsecond=0)
            existing_map[row["notion_page_id"]] = db_time

    except Exception as e:
        logger.error(f"Failed to fetch existing pages: {e}")
        return list(page_map.keys()), []

    # Compare
    new_page_ids = []
    updated_page_ids = []

    for page_id, notion_time in page_map.items():
        if page_id not in existing_map:
            new_page_ids.append(page_id)
        elif notion_time > existing_map[page_id]:
            updated_page_ids.append(page_id)

    logger.info(
        f"Change detection: {len(new_page_ids)} new, {len(updated_page_ids)} updated, "
        f"{len(page_map) - len(new_page_ids) - len(updated_page_ids)} unchanged"
    )

    return new_page_ids, updated_page_ids
```

**Testing:**
1. Initial import: 726 pages → All fetched (expected)
2. Re-import immediately: 0 pages fetched, 726 skipped (success metric)
3. Add 1 page in Notion: 1 fetched, 726 skipped
4. Modify 10 pages: 10 fetched, 716 skipped

**Performance Gains:**
- Current: 9 minutes every import
- After fix: <5 seconds for re-import without changes (99.1% reduction)

### Future Migration Path (When DB > 5,000 pages)

**Trigger:** DB performance degradation or page count > 5,000

**Approach:** Migrate to Solution 3 (Server-side RPC comparison)

**Steps:**
1. Deploy SQL function to Supabase
2. Add environment variable: `USE_RPC_CHANGE_DETECTION=true`
3. Update `get_pages_to_fetch()` to call RPC when enabled
4. Monitor performance (should be ~150ms regardless of DB size)

**No action needed now** - Implement when scaling issues occur.

---

## 🔮 Future Scenario: 여러 부모 페이지 지원 시 영향 분석

### 시나리오 가정

**현재:** 1개 부모 페이지 → 726개 하위 페이지
**미래:** N개 부모 페이지 → 각각 수백 개 하위 페이지

**예시:**
- 부모 페이지 A: 500개 하위 페이지
- 부모 페이지 B: 300개 하위 페이지
- 부모 페이지 C: 200개 하위 페이지
- **총 DB:** 1,000개 페이지

### Import 패턴 변화

#### 패턴 1: 전체 부모 동시 Import
```python
# 사용자가 "모든 부모 동기화" 버튼 클릭
# → 1,000개 전체 페이지 체크
for parent_id in parent_page_ids:
    pages = await fetch_child_pages_from_parent(parent_id)
    # 1,000개 페이지 메타데이터 수집
```

#### 패턴 2: 특정 부모만 선택 Import (더 일반적)
```python
# 사용자가 "부모 A만 동기화" 선택
# → 500개만 체크 (부모 A의 하위 페이지만)
pages = await fetch_child_pages_from_parent(parent_A_id)
# 500개 페이지 메타데이터 수집
```

### 성능 비교: 방식 A vs Solution 3

#### 시나리오 1: 전체 동기화 (1,000개 중 1,000개 체크)

| 항목 | 방식 A (전체 조회) | Solution 3 (RPC) | 승자 |
|------|-------------------|------------------|------|
| DB 쿼리 | 1회 (전체 1,000개) | 1회 (RPC) | 동일 |
| 네트워크 전송 (요청) | 최소 | 1,000 × 80 bytes = 80KB | A |
| 네트워크 전송 (응답) | 1,000 × 60 = 60KB | 변경된 것만 (~10개 = 0.4KB) | **Sol 3** |
| DB 처리 시간 | 150ms (full scan) | 120ms (indexed lookup) | Sol 3 |
| 총 시간 | **~200ms** | **~170ms** | **Sol 3 (약간 빠름)** |

**결론:** 거의 비슷. 방식 A 충분히 빠름.

---

#### 시나리오 2: 특정 부모만 동기화 (1,000개 중 500개만 체크) ⭐ 가장 일반적

| 항목 | 방식 A (전체 조회) | Solution 3 (RPC) | 승자 |
|------|-------------------|------------------|------|
| DB 쿼리 | 1회 (전체 1,000개) | 1회 (RPC, 500개만 비교) | Sol 3 |
| 네트워크 전송 (요청) | 최소 | 500 × 80 = 40KB | A |
| 네트워크 전송 (응답) | **1,000 × 60 = 60KB** ❌ | **변경된 것만 (~5개 = 0.2KB)** ✅ | **Sol 3 (300배 적음)** |
| DB 처리 시간 | 150ms (1,000개 스캔) | 80ms (500개만 lookup) | **Sol 3** |
| 총 시간 | **~200ms** | **~120ms** | **Sol 3 (1.7배 빠름)** |

**결론:** Solution 3이 확실히 유리!

---

#### 시나리오 3: 여러 부모 순차 동기화 (5개 부모 × 각 200개 = 1,000개 DB)

**사용자 워크플로우:**
```
1. 부모 A 동기화 (200개 체크) → 5초 후
2. 부모 B 동기화 (200개 체크) → 1분 후
3. 부모 C 동기화 (200개 체크) → 3분 후
...
```

**방식 A (전체 조회):**
```python
# 매번 1,000개 전체 조회 (불필요한 800개 포함)
동기화 1회당: 200ms
5회 동기화: 5 × 200ms = 1,000ms
불필요한 데이터 전송: 5 × 800개 × 60 bytes = 240KB
```

**Solution 3 (RPC):**
```python
# 매번 200개만 전송 및 비교
동기화 1회당: 100ms
5회 동기화: 5 × 100ms = 500ms
불필요한 데이터 전송: 0KB
```

**성능 차이:**
- 시간: 2배 빠름 (1,000ms vs 500ms)
- 네트워크: 240KB 절약

**결론:** Solution 3 압도적 승리!

---

### 스케일 시나리오 분석

| DB 크기 | 부모 개수 | Import 크기 (단일 부모) | Import/DB 비율 | 방식 A | Solution 3 | 승자 |
|---------|----------|----------------------|--------------|--------|-----------|------|
| 1,000 | 2 | 500 | 50% | 200ms | **120ms** | **Sol 3** |
| 5,000 | 5 | 1,000 | 20% | 600ms | **150ms** | **Sol 3 (4배)** |
| 10,000 | 10 | 1,000 | 10% | 1,200ms | **150ms** | **Sol 3 (8배)** |
| 50,000 | 20 | 2,500 | 5% | 5,000ms | **200ms** | **Sol 3 (25배)** |

**패턴:** DB가 크고, Import가 부분 집합일수록 Solution 3이 압도적으로 유리!

---

### 결론: 여러 부모 페이지 시나리오에서는?

#### ✅ Solution 3을 처음부터 구현하는 것이 정답!

**이유:**

1. **부분 Import가 일반적:**
   - 사용자는 보통 "전체 동기화"보다 "특정 부모만 동기화" 사용
   - 방식 A는 매번 전체 DB를 가져옴 (비효율)

2. **네트워크 대역폭 절약:**
   - DB 10,000개, Import 1,000개 시: 60KB vs 0.5KB (120배 차이)
   - 모바일/저대역폭 환경에서 유리

3. **DB 처리 시간 단축:**
   - Full scan: O(전체 DB 크기)
   - RPC: O(Import 크기)

4. **확장성:**
   - 부모 100개, DB 100,000개로 증가해도 성능 유지
   - 방식 A는 수 초 단위로 느려짐

5. **사용자 경험:**
   - "부모 A 동기화" 클릭 → 즉시 완료 (100ms)
   - vs. "부모 A 동기화" 클릭 → 1초 대기 (전체 DB 스캔)

---

### 수정된 권장사항

#### 현재 (단일 부모 페이지만 지원)
- **방식 A 구현** (간단, 충분히 빠름)
- 이유: DB 732 vs Import 726 (99% 중복)

#### 미래 (여러 부모 페이지 지원 예정)
- **Solution 3을 지금 바로 구현** ⭐ (추천!)
- 이유:
  1. 부분 Import 시 3~25배 빠름
  2. 네트워크 대역폭 120배 절약
  3. 확장성 보장
  4. 한 번만 구현하면 됨

---

### 구현 비용 비교

| 방식 | 구현 시간 | 코드 라인 수 | 인프라 변경 | 유지보수 |
|------|----------|------------|-----------|---------|
| 방식 A | 30분 | +50 lines | 없음 | 쉬움 |
| Solution 3 | 2시간 | +120 lines | SQL 함수 배포 | 중간 |
| 차이 | +1.5시간 | +70 lines | SQL 1개 | 약간 복잡 |

**추가 비용:** 1.5시간 개발 시간

**얻는 것:**
- 미래 확장성 보장
- 부분 Import 시 3~25배 성능 향상
- 네트워크 대역폭 100배 절약
- 재작업 불필요 (한 번에 끝)

---

### 최종 권장사항 (여러 부모 페이지 고려 시)

**지금 바로 Solution 3 구현!**

**구현 단계:**

1. **Supabase SQL 함수 배포:**
```sql
CREATE OR REPLACE FUNCTION get_changed_pages(pages_data jsonb)
RETURNS jsonb AS $$
DECLARE
    result jsonb;
    new_ids text[];
    updated_ids text[];
    page_record jsonb;
    notion_id text;
    notion_time timestamptz;
    db_time timestamptz;
BEGIN
    new_ids := ARRAY[]::text[];
    updated_ids := ARRAY[]::text[];

    FOR page_record IN SELECT * FROM jsonb_array_elements(pages_data)
    LOOP
        notion_id := page_record->>'id';
        notion_time := (page_record->>'last_edited')::timestamptz;

        SELECT notion_last_edited_time INTO db_time
        FROM raw_notes
        WHERE notion_page_id = notion_id;

        IF NOT FOUND THEN
            new_ids := array_append(new_ids, notion_id);
        ELSIF notion_time > db_time THEN
            updated_ids := array_append(updated_ids, notion_id);
        END IF;
    END LOOP;

    result := jsonb_build_object(
        'new_page_ids', to_jsonb(new_ids),
        'updated_page_ids', to_jsonb(updated_ids)
    );

    RETURN result;
END;
$$ LANGUAGE plpgsql;
```

2. **Python 코드 업데이트:**
```python
# backend/services/supabase_service.py
async def get_pages_to_fetch(
    self,
    notion_pages: List[Dict[str, Any]]
) -> tuple[List[str], List[str]]:
    """Compare Notion pages with DB using server-side RPC."""
    await self._ensure_initialized()

    # Prepare data for RPC
    pages_json = []
    for p in notion_pages:
        page_id = p.get("id")
        last_edited = p.get("last_edited_time")
        if not page_id or not last_edited:
            continue

        # Truncate to seconds for comparison
        notion_time = datetime.fromisoformat(last_edited.replace("Z", "+00:00"))
        notion_time = notion_time.replace(microsecond=0)

        pages_json.append({
            "id": page_id,
            "last_edited": notion_time.isoformat()
        })

    if not pages_json:
        return [], []

    try:
        # Call RPC function (HTTP POST, no URL limit)
        response = await self.client.rpc('get_changed_pages', {
            'pages_data': pages_json
        }).execute()

        result = response.data
        new_page_ids = result.get('new_page_ids', [])
        updated_page_ids = result.get('updated_page_ids', [])

        logger.info(
            f"Change detection: {len(new_page_ids)} new, {len(updated_page_ids)} updated, "
            f"{len(pages_json) - len(new_page_ids) - len(updated_page_ids)} unchanged"
        )

        return new_page_ids, updated_page_ids

    except Exception as e:
        logger.error(f"RPC change detection failed: {e}")
        # Fallback: treat all as new
        return [p["id"] for p in pages_json], []
```

3. **테스트:**
   - 초기 import: 726개 전체 fetch
   - 재실행: 0개 fetch, 726 skipped
   - 1개 추가: 1개 fetch
   - 향후 부모 B 추가 시: 부모 B만 fetch

**ROI (투자 대비 효과):**
- 투자: 1.5시간 개발 시간
- 효과:
  - 현재: 방식 A와 비슷 (110ms vs 150ms)
  - 미래: 3~25배 빠름 + 재작업 불필요

**결론:** 여러 부모 페이지 지원 예정이라면 **Solution 3을 지금 구현하는 것이 현명함!**

---

## 🔴 CRITICAL PRE-IMPLEMENTATION VERIFICATION REQUIRED

### Must-Test Before Implementation

**Test 1: Verify last_edited_time reflects content changes**

```bash
# Manual test procedure:
1. Notion에서 임의의 페이지 선택 (예: 첫 번째 페이지)
2. 페이지 ID 확인
3. API로 현재 last_edited_time 확인
4. Notion에서 페이지 내용 수정 (텍스트 추가)
5. 1분 대기 (Notion indexing)
6. API로 다시 last_edited_time 확인
7. 비교: 수정 전 vs 수정 후

Expected: last_edited_time이 변경됨
If NOT: Plan 무효화, 대안 필요
```

**Test Code:**
```python
import asyncio
from services.notion_service import NotionService
from datetime import datetime

async def test_last_edited_time_accuracy():
    service = NotionService()
    parent_id = os.getenv("NOTION_PARENT_PAGE_ID")

    # Get first page
    pages = await service.fetch_child_pages_from_parent(parent_id, page_size=1)
    if not pages:
        print("No pages found")
        return

    page = pages[0]
    page_id = page["id"]
    initial_time = page["last_edited_time"]

    print(f"Page ID: {page_id}")
    print(f"Initial last_edited_time: {initial_time}")
    print(f"\n⚠️  NOW: Go to Notion and edit this page's content")
    print(f"URL: https://notion.so/{page_id.replace('-', '')}")
    print(f"\nPress Enter after editing...")
    input()

    # Fetch again
    pages_after = await service.fetch_child_pages_from_parent(parent_id, page_size=100)
    page_after = next(p for p in pages_after if p["id"] == page_id)
    final_time = page_after["last_edited_time"]

    print(f"\nFinal last_edited_time: {final_time}")
    print(f"Changed: {initial_time != final_time}")

    if initial_time == final_time:
        print("\n❌ CRITICAL: last_edited_time NOT updated after content change!")
        print("   Plan is NOT viable. Need alternative approach.")
    else:
        print("\n✅ SUCCESS: last_edited_time updated correctly!")
        print("   Plan is viable. Proceed with implementation.")

asyncio.run(test_last_edited_time_accuracy())
```

**Decision Point:**
- ✅ If test passes → Proceed with implementation
- ❌ If test fails → Switch to Alternative Plan B

---

## Alternative Plan B: If last_edited_time Not Reliable

**Approach:** Use `pages.retrieve()` API for accurate timestamps

**Changes to Plan:**

### Phase 1: Metadata Fetch with pages.retrieve()
```python
async def fetch_child_pages_with_accurate_timestamps(
    self,
    parent_page_id: str
) -> List[Dict]:
    """Fetch child pages with accurate last_edited_time."""

    # Step 1: Get child page IDs (lightweight)
    child_blocks = await self.fetch_child_pages_from_parent(parent_page_id)

    # Step 2: Batch retrieve full page objects (for accurate timestamps)
    accurate_pages = []
    for page in child_blocks:
        page_id = page["id"]

        # Call pages.retrieve() API
        try:
            await self.rate_limiter.acquire()
            full_page = await asyncio.to_thread(
                self.client.pages.retrieve,
                page_id=page_id
            )
            accurate_pages.append(full_page)
        except Exception as e:
            logger.warning(f"Failed to retrieve page {page_id}: {e}")
            # Fallback to block timestamp
            accurate_pages.append(page)

    return accurate_pages
```

**Impact:**
- API calls: 724 (one per page for metadata)
- Time: ~241 seconds (4 minutes) at 3 req/sec
- Still faster than content fetch (no block content)
- More API quota usage

**Trade-off Analysis:**
- Current full fetch: 724 content calls (~9 min)
- Plan A (incremental, if viable): 0-100 content calls (~0-30 sec)
- Plan B (pages.retrieve): 724 metadata + N content calls (~4 min + N×0.33 sec)

**Example Scenarios (Plan B):**
- 1 page changed: 724 metadata + 1 content = ~4 min
- 10 pages changed: 724 metadata + 10 content = ~4 min
- No changes: 724 metadata + 0 content = ~4 min

**Conclusion Plan B:**
- NOT as efficient as Plan A
- But still ~2x faster than current (9 min vs 4 min)
- Guaranteed accuracy

---

## Summary of Critical Issues

| Issue | Severity | Impact | Solution Status |
|-------|----------|--------|-----------------|
| 1. Microseconds mismatch | HIGH | False positives | ✅ Solved (truncate) |
| 2. Block vs Page timestamp | **CRITICAL** | Plan viability | ⚠️  **NEEDS TESTING** |
| 3. DB timestamp source | MEDIUM | Consistency | ✅ Same as Issue 2 |
| 4. Timezone handling | MEDIUM | Comparison errors | ✅ Solved (ensure aware) |
| 5. Race condition | LOW | Missed updates | ✅ Accepted (eventual) |
| 6. Pagination duplicates | LOW | Minor inefficiency | ✅ Solved (dedupe) |
| 7. Memory/query limits | LOW | Scale issues | ✅ Solved (batch) |
| 8. DB index duplicates | LOW | Logic errors | ✅ Solved (handle) |

**Next Step:**
**USER MUST RUN TEST 1 BEFORE PROCEEDING WITH IMPLEMENTATION**

If Test 1 passes → Use Plan A (original incremental update)
If Test 1 fails → Use Plan B (pages.retrieve for timestamps)

---

## 📋 Solution 3 구현 계획 (심층 분석)

### 🤔 구현 전 깊은 고민 사항

#### 1. SQL 함수 설계 시 고려사항

**문제 1: JSON 데이터 크기 제한**
```sql
-- PostgreSQL jsonb 크기: 메모리 제한만 (실질적으로 무제한)
-- HTTP POST body: Supabase 기본 10MB
-- 현재: 726개 × 80 bytes = 58KB ✅ 안전
-- 미래: 10,000개 × 80 bytes = 800KB ✅ 여전히 안전
```
✅ **결론:** 크기 제한 문제 없음

**문제 2: Timestamp 형식 불일치**
```python
# Python → SQL
notion_time.isoformat()  # "2024-01-15T14:30:00+00:00"

# SQL 파싱
(page_record->>'last_edited')::timestamptz
# PostgreSQL이 ISO 8601 자동 인식 ✅
```
✅ **결론:** 형식 호환 보장됨

**문제 3: Microsecond 정밀도**
```sql
-- Notion: 밀리초 (000)
-- DB: 마이크로초 (123456)

-- 해결: SQL에서 truncate
date_trunc('second', notion_time) > date_trunc('second', db_time)
```
✅ **결론:** SQL 함수 내에서 처리 → Python 코드 간소화

**문제 4: 인덱스 활용**
```sql
-- 기존 인덱스
CREATE INDEX idx_raw_notes_notion_page_id ON raw_notes(notion_page_id);

-- SQL 함수
WHERE notion_page_id = notion_id;  -- ✅ 인덱스 자동 활용

-- 성능: O(log n) × 726번 = 빠름
```
✅ **결론:** 추가 인덱스 불필요

**문제 5: NULL 처리**
```sql
-- 스키마 확인
notion_last_edited_time TIMESTAMPTZ NOT NULL,  -- ✅ NOT NULL 제약
```
✅ **결론:** NULL 처리 불필요

---

#### 2. Python 코드 설계 시 고려사항

**문제 1: RPC 실패 시 Fallback 전략**

**Option A: 전체를 "new"로 처리 (현재)**
```python
except Exception:
    return [all page ids], []  # 비효율적
```
❌ **문제:** 전체 재import (9분)

**Option B: 방식 A로 Fallback (제안)**
```python
except Exception:
    # Full table scan으로 전환
    return await self._full_scan_fallback(...)
```
✅ **장점:** RPC 실패해도 최적 성능 유지

**결정:** Option B 채택

---

**문제 2: Timestamp 파싱 에러 처리**

**현재:**
```python
try:
    notion_time = parse(last_edited)
except ValueError:
    continue  # Skip → 동기화 안 됨! ❌
```

**개선:**
```python
except ValueError:
    logger.warning(f"{page_id} has invalid timestamp, treating as new")
    force_new_ids.append(page_id)  # ✅ 동기화 보장
```

---

**문제 3: RPC 응답 검증**

**필수 검증 항목:**
1. 응답 형식 (`dict`)
2. 필수 키 존재 (`new_page_ids`, `updated_page_ids`)
3. 값 타입 (`list`)
4. UUID 형식 (정규표현식)
5. SQL 에러 체크 (`error` 키)

```python
# 검증 로직
if 'error' in result:
    raise ValueError(f"SQL error: {result['error']}")

if not isinstance(new_ids, list):
    raise ValueError("Invalid response type")

UUID_PATTERN = re.compile(r'^[0-9a-f-]{36}$')
for id in new_ids:
    if not UUID_PATTERN.match(id):
        raise ValueError(f"Invalid UUID: {id}")
```

---

**문제 4: 성능 측정**

```python
import time

start = time.time()
response = await self.client.rpc(...)
elapsed = time.time() - start

logger.info(f"RPC completed in {elapsed:.2f}s")
```

**목적:**
- 성능 모니터링
- 최적화 효과 확인
- 문제 조기 발견

---

#### 3. SQL 함수 배포 전략

**문제 1: 버전 관리**

**방법 1: SQL Editor (간단, 버전 관리 안 됨)**
- Supabase Dashboard → SQL Editor → 실행

**방법 2: 마이그레이션 파일 (추천)**
```bash
backend/docs/supabase_migrations/001_get_changed_pages.sql
```

✅ **장점:**
- Git 버전 관리
- 팀원 공유 쉬움
- Dev/Staging/Prod 재배포 가능

**결정:** 방법 2 채택 (파일로 관리)

---

**문제 2: 배포 전 테스트**

**테스트 SQL:**
```sql
-- 1. 신규 페이지
SELECT get_changed_pages('[
    {"id": "new-page-id", "last_edited": "2024-01-15T14:30:00+00:00"}
]'::jsonb);
-- 예상: {"new_page_ids": ["new-page-id"]}

-- 2. 기존 페이지 (변경 없음)
SELECT get_changed_pages('[
    {"id": "existing-unchanged", "last_edited": "기존 timestamp"}
]'::jsonb);
-- 예상: {"new_page_ids": [], "updated_page_ids": []}

-- 3. 수정된 페이지
SELECT get_changed_pages('[
    {"id": "existing-modified", "last_edited": "최신 timestamp"}
]'::jsonb);
-- 예상: {"updated_page_ids": ["existing-modified"]}

-- 4. 빈 배열
SELECT get_changed_pages('[]'::jsonb);
-- 예상: 에러 없이 빈 결과

-- 5. 잘못된 형식
SELECT get_changed_pages('[{"invalid": "data"}]'::jsonb);
-- 예상: EXCEPTION 처리로 에러 정보 반환
```

**필수 확인 항목:**
✅ 신규 페이지 감지
✅ 수정 페이지 감지
✅ 변경 없는 페이지 skip
✅ 빈 입력 처리
✅ 에러 핸들링

---

#### 4. 에러 시나리오 대응

**시나리오 1: SQL 함수 미배포**
```python
try:
    response = await self.client.rpc('get_changed_pages', ...)
except Exception as e:
    if "does not exist" in str(e).lower():
        logger.error("RPC function not deployed!")
        # Fallback to 방식 A
```

**시나리오 2: SQL 실행 오류**
```sql
EXCEPTION
    WHEN OTHERS THEN
        result := jsonb_build_object(
            'error', SQLERRM,  -- 에러 메시지 포함
            'new_page_ids', '[]'::jsonb,
            'updated_page_ids', '[]'::jsonb
        );
        RETURN result;
```

**시나리오 3: 네트워크 타임아웃**
```python
import asyncio

try:
    response = await asyncio.wait_for(
        self.client.rpc(...),
        timeout=30.0  # 30초
    )
except asyncio.TimeoutError:
    logger.error("RPC timeout, using fallback")
    # Fallback
```

---

#### 5. 성능 최적화 고려사항

**현재 구현: LOOP 방식**
```sql
FOR page_record IN SELECT * FROM jsonb_array_elements(...)
LOOP
    SELECT ... WHERE notion_page_id = notion_id;  -- 726번 실행
    ...
END LOOP;
```
- **성능:** O(n log n) - 인덱스 활용
- **시간:** ~100ms (726개)
- **장점:** 코드 간단, 이해 쉬움

**대안: Bulk JOIN 방식**
```sql
WITH notion_data AS (
    SELECT
        elem->>'id' AS notion_id,
        date_trunc('second', (elem->>'last_edited')::timestamptz) AS notion_time
    FROM jsonb_array_elements(pages_data) AS elem
)
SELECT
    nd.notion_id,
    CASE
        WHEN rn.notion_page_id IS NULL THEN 'new'
        WHEN nd.notion_time > date_trunc('second', rn.notion_last_edited_time) THEN 'updated'
        ELSE 'unchanged'
    END AS status
FROM notion_data nd
LEFT JOIN raw_notes rn ON rn.notion_page_id = nd.notion_id;
```
- **성능:** O(n) - 한 번의 JOIN
- **시간:** ~80ms 예상
- **단점:** 코드 복잡, 디버깅 어려움

**결정:**
- **Phase 1:** LOOP 방식 구현 (간단)
- **Phase 2 (선택):** 성능 이슈 시 JOIN 방식으로 최적화

---

### 📝 구현 순서 (Phase별 상세)

#### Phase 1: SQL 함수 생성 (30분)

**파일:** `backend/docs/supabase_migrations/001_get_changed_pages.sql`

```sql
-- Incremental Import: Change Detection Function
-- Created: 2024-01-XX
-- Purpose: 노션 페이지 메타데이터와 DB 비교하여 변경 감지

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

        -- DB에서 기존 페이지 조회 (인덱스 활용)
        SELECT date_trunc('second', notion_last_edited_time) INTO db_time
        FROM raw_notes
        WHERE notion_page_id = notion_id;

        -- 비교 및 분류
        IF NOT FOUND THEN
            -- 신규 페이지
            new_ids := array_append(new_ids, notion_id);
        ELSIF notion_time > db_time THEN
            -- 수정된 페이지 (Notion timestamp가 더 최신)
            updated_ids := array_append(updated_ids, notion_id);
        END IF;
        -- ELSE: unchanged (skip)
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
        -- Python에서 Fallback 로직 작동
        result := jsonb_build_object(
            'error', SQLERRM,
            'error_detail', SQLSTATE,
            'new_page_ids', '[]'::jsonb,
            'updated_page_ids', '[]'::jsonb
        );
        RETURN result;
END;
$$;

-- 함수 설명
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

에러 시:
{
  "error": "에러 메시지",
  "new_page_ids": [],
  "updated_page_ids": []
}
';
```

**배포 절차:**
1. Supabase Dashboard → SQL Editor
2. 위 SQL 복사 → Run
3. 확인: `SELECT * FROM pg_proc WHERE proname = 'get_changed_pages';`
4. Git commit

---

#### Phase 2: Python 코드 재구현 (45분)

**파일:** `backend/services/supabase_service.py`
**라인:** 881-979 (기존 `get_pages_to_fetch()` 전체 교체)

```python
async def get_pages_to_fetch(
    self,
    notion_pages: List[Dict[str, Any]]
) -> tuple[List[str], List[str]]:
    """
    Compare Notion pages with DB using server-side RPC.

    Uses PostgreSQL function for efficient change detection.
    Falls back to full table scan if RPC fails.

    Args:
        notion_pages: List of page metadata from Notion API
            Each page must have: id, last_edited_time

    Returns:
        Tuple of (new_page_ids, updated_page_ids)

    Performance:
        - RPC mode: ~150ms (constant time, scales to 100k pages)
        - Fallback mode: ~110ms (current size)
        - Network: Only changed pages (0.5KB vs 60KB)

    Example:
        >>> pages = [{"id": "abc", "last_edited_time": "2024-01-15T14:30:00.000Z"}]
        >>> new, updated = await service.get_pages_to_fetch(pages)
        >>> print(f"New: {len(new)}, Updated: {len(updated)}")
    """
    await self._ensure_initialized()

    # Prepare data for RPC
    pages_json = []
    force_new_ids = []  # Pages with invalid timestamps → treat as new

    for p in notion_pages:
        page_id = p.get("id")
        last_edited = p.get("last_edited_time")

        if not page_id:
            logger.warning("Page missing 'id' field, skipping")
            continue

        if not last_edited:
            logger.warning(f"Page {page_id} missing 'last_edited_time', treating as new")
            force_new_ids.append(page_id)
            continue

        try:
            # Parse ISO 8601 timestamp
            notion_time = datetime.fromisoformat(last_edited.replace("Z", "+00:00"))
            
            # Truncate to seconds (match SQL function behavior)
            notion_time = notion_time.replace(microsecond=0)

            pages_json.append({
                "id": page_id,
                "last_edited": notion_time.isoformat()
            })
        except (ValueError, AttributeError, TypeError) as e:
            logger.warning(f"Invalid timestamp for {page_id}: {e}, treating as new")
            force_new_ids.append(page_id)

    if not pages_json and not force_new_ids:
        logger.warning("No valid pages to check")
        return [], []

    logger.info(f"Change detection: checking {len(pages_json)} pages via RPC (sample: {[p['id'] for p in pages_json[:3]]})")

    # Try RPC change detection (Solution 3)
    try:
        import time
        start_time = time.time()

        response = await self.client.rpc('get_changed_pages', {
            'pages_data': pages_json
        }).execute()

        elapsed = time.time() - start_time

        # Validate response structure
        if not response.data or not isinstance(response.data, dict):
            raise ValueError("Invalid RPC response format: expected dict")

        result = response.data

        # Check for SQL function error
        if 'error' in result:
            raise ValueError(f"SQL function error: {result['error']} (SQLSTATE: {result.get('error_detail', 'unknown')})")

        # Extract results
        new_page_ids = result.get('new_page_ids', [])
        updated_page_ids = result.get('updated_page_ids', [])

        # Validate types
        if not isinstance(new_page_ids, list):
            raise ValueError(f"Invalid type for new_page_ids: {type(new_page_ids)}")
        if not isinstance(updated_page_ids, list):
            raise ValueError(f"Invalid type for updated_page_ids: {type(updated_page_ids)}")

        # Add force_new pages
        new_page_ids.extend(force_new_ids)

        # Validate UUIDs
        import re
        UUID_PATTERN = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)
        
        for page_id in new_page_ids + updated_page_ids:
            if not UUID_PATTERN.match(page_id):
                raise ValueError(f"Invalid UUID format: {page_id}")

        logger.info(
            f"✅ RPC change detection completed in {elapsed:.2f}s: "
            f"{len(new_page_ids)} new, {len(updated_page_ids)} updated, "
            f"{result.get('unchanged_count', len(pages_json) - len(new_page_ids) - len(updated_page_ids))} unchanged"
        )

        return new_page_ids, updated_page_ids

    except Exception as rpc_error:
        logger.error(f"❌ RPC change detection failed: {rpc_error}, falling back to full table scan")

        # Fallback: Full table scan (방식 A)
        try:
            logger.info("Using fallback: full table scan")
            
            response = await (
                self.client.table("raw_notes")
                .select("notion_page_id, notion_last_edited_time")
                .execute()
            )

            # Build existing_map from DB
            existing_map = {}
            for row in response.data:
                db_page_id = row["notion_page_id"]
                db_time = row["notion_last_edited_time"]

                # Parse timestamp
                if isinstance(db_time, str):
                    db_time = datetime.fromisoformat(db_time.replace("Z", "+00:00"))

                # Ensure timezone-aware
                if db_time.tzinfo is None:
                    db_time = db_time.replace(tzinfo=timezone.utc)

                # Truncate to seconds
                db_time = db_time.replace(microsecond=0)
                existing_map[db_page_id] = db_time

            # Build page_map from Notion pages
            page_map = {}
            for p_json in pages_json:
                page_id = p_json["id"]
                notion_time = datetime.fromisoformat(p_json["last_edited"])
                page_map[page_id] = notion_time

            # Compare
            new_ids = []
            updated_ids = []

            for page_id, notion_time in page_map.items():
                if page_id not in existing_map:
                    new_ids.append(page_id)
                elif notion_time > existing_map[page_id]:
                    updated_ids.append(page_id)

            # Add force_new pages
            new_ids.extend(force_new_ids)

            logger.info(
                f"✅ Fallback completed: {len(new_ids)} new, {len(updated_ids)} updated, "
                f"{len(page_map) - len(new_ids) - len(updated_ids)} unchanged"
            )

            return new_ids, updated_ids

        except Exception as fallback_error:
            logger.error(f"❌ Fallback also failed: {fallback_error}, treating all as new (last resort)")
            
            # Last resort: treat all as new
            all_ids = [p["id"] for p in pages_json] + force_new_ids
            return all_ids, []
```

**변경 사항:**
- ✅ RPC 우선 사용
- ✅ 상세한 검증 (응답 형식, UUID)
- ✅ 3단계 Fallback (RPC → 방식 A → 전체 new)
- ✅ 성능 측정 및 로깅
- ✅ 에러별 명확한 로그

---

#### Phase 3: Startup 검증 추가 (15분)

**파일 1:** `backend/services/supabase_service.py` (메서드 추가)

```python
async def validate_rpc_function_exists(self) -> bool:
    """
    Check if RPC function is deployed in Supabase.
    
    Returns:
        bool: True if function exists and works, False otherwise
    """
    try:
        # Test with empty array
        response = await self.client.rpc('get_changed_pages', {
            'pages_data': []
        }).execute()
        
        # Validate response
        if not response.data or not isinstance(response.data, dict):
            logger.warning("⚠️  RPC function returned unexpected format")
            return False
            
        logger.info("✅ RPC function 'get_changed_pages' is available and working")
        return True
        
    except Exception as e:
        logger.warning(f"⚠️  RPC function 'get_changed_pages' not available: {e}")
        logger.warning("   Import will use fallback mode (full table scan)")
        return False
```

**파일 2:** `backend/config.py` (설정 추가)

```python
class Settings(BaseSettings):
    # ... 기존 설정 ...
    
    VALIDATE_RPC_ON_STARTUP: bool = Field(
        default=True,
        description="Validate RPC function availability on startup"
    )
```

**파일 3:** `backend/main.py` (startup 이벤트 추가)

```python
@app.on_event("startup")
async def startup_validation():
    """Validate critical dependencies on startup."""
    logger.info("=" * 80)
    logger.info("STARTUP VALIDATION")
    logger.info("=" * 80)
    
    supabase_service = SupabaseService()
    
    # Validate RPC function
    if settings.validate_rpc_on_startup:
        await supabase_service.validate_rpc_function_exists()
    
    logger.info("=" * 80)
```

---

#### Phase 4: 단위 테스트 작성 (45분)

**파일:** `backend/tests/unit/test_supabase_change_detection_rpc.py` (새로 생성)

```python
"""
Unit tests for RPC-based change detection.
Tests the get_pages_to_fetch() method with Solution 3 (RPC).
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_rpc_detects_new_pages(supabase_service):
    """신규 페이지 감지 테스트"""
    notion_pages = [
        {"id": "new-page-1", "last_edited_time": "2024-01-15T14:30:00.000Z"},
        {"id": "new-page-2", "last_edited_time": "2024-01-15T15:00:00.000Z"}
    ]

    new_ids, updated_ids = await supabase_service.get_pages_to_fetch(notion_pages)

    assert "new-page-1" in new_ids
    assert "new-page-2" in new_ids
    assert len(updated_ids) == 0

@pytest.mark.asyncio
async def test_rpc_detects_updated_pages(supabase_service):
    """수정된 페이지 감지 테스트"""
    # Setup: Insert existing page with old timestamp
    old_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    await supabase_service.upsert_raw_note({
        "notion_page_id": "existing-page",
        "notion_url": "https://notion.so/existing-page",
        "title": "Test Page",
        "content": "Old content",
        "properties_json": {},
        "notion_created_time": old_time,
        "notion_last_edited_time": old_time
    })

    # Test: Notion shows newer timestamp
    notion_pages = [{
        "id": "existing-page",
        "last_edited_time": "2024-01-15T14:30:00.000Z"
    }]

    new_ids, updated_ids = await supabase_service.get_pages_to_fetch(notion_pages)

    assert len(new_ids) == 0
    assert "existing-page" in updated_ids

@pytest.mark.asyncio
async def test_rpc_skips_unchanged_pages(supabase_service):
    """변경 없는 페이지 skip 테스트"""
    timestamp = datetime(2024, 1, 15, 14, 30, 0, tzinfo=timezone.utc)

    # Setup: Insert page with exact timestamp
    await supabase_service.upsert_raw_note({
        "notion_page_id": "unchanged-page",
        "notion_url": "https://notion.so/unchanged-page",
        "title": "Unchanged",
        "content": "Same content",
        "properties_json": {},
        "notion_created_time": timestamp,
        "notion_last_edited_time": timestamp
    })

    # Test: Same timestamp in Notion
    notion_pages = [{
        "id": "unchanged-page",
        "last_edited_time": timestamp.isoformat()
    }]

    new_ids, updated_ids = await supabase_service.get_pages_to_fetch(notion_pages)

    assert len(new_ids) == 0
    assert len(updated_ids) == 0
    # Page should be skipped

@pytest.mark.asyncio
async def test_rpc_handles_mixed_scenarios(supabase_service):
    """신규 + 수정 + 변경없음 혼합 시나리오"""
    old_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    same_time = datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc)

    # Setup: 2 existing pages
    await supabase_service.upsert_raw_note({
        "notion_page_id": "old-page",
        "notion_url": "https://notion.so/old",
        "title": "Old",
        "content": "Old",
        "properties_json": {},
        "notion_created_time": old_time,
        "notion_last_edited_time": old_time
    })
    await supabase_service.upsert_raw_note({
        "notion_page_id": "same-page",
        "notion_url": "https://notion.so/same",
        "title": "Same",
        "content": "Same",
        "properties_json": {},
        "notion_created_time": same_time,
        "notion_last_edited_time": same_time
    })

    # Test: 1 new, 1 updated, 1 unchanged
    notion_pages = [
        {"id": "new-page", "last_edited_time": "2024-01-15T14:30:00.000Z"},  # New
        {"id": "old-page", "last_edited_time": "2024-01-15T15:00:00.000Z"},  # Updated
        {"id": "same-page", "last_edited_time": same_time.isoformat()}  # Unchanged
    ]

    new_ids, updated_ids = await supabase_service.get_pages_to_fetch(notion_pages)

    assert "new-page" in new_ids
    assert "old-page" in updated_ids
    assert "same-page" not in new_ids
    assert "same-page" not in updated_ids

@pytest.mark.asyncio
async def test_rpc_fallback_on_function_missing(supabase_service, monkeypatch):
    """RPC 함수 없을 때 Fallback 작동 테스트"""
    # Mock: RPC 호출 실패 (function does not exist)
    async def mock_rpc(*args, **kwargs):
        raise Exception("function get_changed_pages does not exist")

    monkeypatch.setattr(supabase_service.client, "rpc", mock_rpc)

    notion_pages = [
        {"id": "test-page", "last_edited_time": "2024-01-15T14:30:00.000Z"}
    ]

    # Should not raise, should use fallback
    new_ids, updated_ids = await supabase_service.get_pages_to_fetch(notion_pages)

    assert isinstance(new_ids, list)
    assert isinstance(updated_ids, list)
    # Fallback treats unknown pages as new
    assert "test-page" in new_ids

@pytest.mark.asyncio
async def test_rpc_handles_invalid_timestamp(supabase_service):
    """잘못된 timestamp 형식 처리 테스트"""
    notion_pages = [
        {"id": "valid-page", "last_edited_time": "2024-01-15T14:30:00.000Z"},
        {"id": "invalid-page", "last_edited_time": "invalid-format"},
        {"id": "missing-page"}  # No last_edited_time
    ]

    new_ids, updated_ids = await supabase_service.get_pages_to_fetch(notion_pages)

    # All should be treated as new (invalid = force new)
    assert "valid-page" in new_ids
    assert "invalid-page" in new_ids
    assert "missing-page" in new_ids

@pytest.mark.asyncio
async def test_rpc_validates_uuid_format(supabase_service, monkeypatch):
    """UUID 형식 검증 테스트"""
    # Mock: RPC returns invalid UUID
    async def mock_rpc(*args, **kwargs):
        mock_response = MagicMock()
        mock_response.data = {
            "new_page_ids": ["not-a-uuid", "also-invalid"],
            "updated_page_ids": []
        }
        return mock_response

    monkeypatch.setattr(supabase_service.client, "rpc", mock_rpc)

    notion_pages = [{"id": "test", "last_edited_time": "2024-01-15T14:30:00.000Z"}]

    # Should fallback due to invalid UUID
    new_ids, updated_ids = await supabase_service.get_pages_to_fetch(notion_pages)

    # Fallback mode should work
    assert isinstance(new_ids, list)
    assert isinstance(updated_ids, list)

@pytest.mark.asyncio
async def test_rpc_handles_empty_input(supabase_service):
    """빈 입력 처리 테스트"""
    notion_pages = []

    new_ids, updated_ids = await supabase_service.get_pages_to_fetch(notion_pages)

    assert new_ids == []
    assert updated_ids == []

@pytest.mark.asyncio
async def test_rpc_handles_sql_error(supabase_service, monkeypatch):
    """SQL 함수 에러 처리 테스트"""
    # Mock: SQL function returns error
    async def mock_rpc(*args, **kwargs):
        mock_response = MagicMock()
        mock_response.data = {
            "error": "division by zero",
            "error_detail": "22012",
            "new_page_ids": [],
            "updated_page_ids": []
        }
        return mock_response

    monkeypatch.setattr(supabase_service.client, "rpc", mock_rpc)

    notion_pages = [{"id": "test", "last_edited_time": "2024-01-15T14:30:00.000Z"}]

    # Should fallback due to SQL error
    new_ids, updated_ids = await supabase_service.get_pages_to_fetch(notion_pages)

    assert isinstance(new_ids, list)
    assert isinstance(updated_ids, list)
```

---

#### Phase 5: 통합 테스트 실행 (30분)

**파일:** `backend/tests/integration/test_incremental_import_rpc.py` (새로 생성)

```python
"""
Integration tests for RPC-based incremental import.
Tests end-to-end import flow with real API calls.
"""
import pytest
from httpx import AsyncClient
import asyncio

@pytest.mark.integration
@pytest.mark.asyncio
async def test_initial_import_all_pages(client: AsyncClient):
    """초기 import: 모든 페이지 가져오기"""
    response = await client.post("/pipeline/import-from-notion?page_size=100")
    
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    # Wait for completion
    for _ in range(60):  # 최대 10분
        status_response = await client.get(f"/pipeline/import-status/{job_id}")
        job = status_response.json()
        
        if job["status"] == "completed":
            break
        elif job["status"] == "failed":
            pytest.fail(f"Job failed: {job.get('error_message')}")
        
        await asyncio.sleep(10)
    
    # Assertions
    assert job["status"] == "completed"
    assert job["imported_pages"] == 726  # All pages
    assert job["skipped_pages"] == 0  # None skipped (first import)
    assert job["processed_pages"] == 726

@pytest.mark.integration
@pytest.mark.asyncio
async def test_reimport_without_changes(client: AsyncClient):
    """재실행: 변경 없음 (전체 skip 예상)"""
    # Run import twice
    response1 = await client.post("/pipeline/import-from-notion?page_size=100")
    job_id_1 = response1.json()["job_id"]
    
    # Wait for first to complete
    await wait_for_job(client, job_id_1)
    
    # Run again immediately
    response2 = await client.post("/pipeline/import-from-notion?page_size=100")
    job_id_2 = response2.json()["job_id"]
    
    await wait_for_job(client, job_id_2)
    
    # Check second job
    status = await client.get(f"/pipeline/import-status/{job_id_2}")
    job = status.json()
    
    # Assertions
    assert job["status"] == "completed"
    assert job["imported_pages"] == 0  # ✅ Nothing changed
    assert job["skipped_pages"] == 726  # ✅ All skipped
    assert job["processed_pages"] == 726

@pytest.mark.integration
@pytest.mark.asyncio
async def test_import_with_one_new_page(client: AsyncClient, notion_api_mock):
    """1개 페이지 추가 후 재실행"""
    # First import
    response1 = await client.post("/pipeline/import-notion?page_size=100")
    await wait_for_job(client, response1.json()["job_id"])
    
    # Simulate: User adds 1 page in Notion
    notion_api_mock.add_page({
        "id": "new-page-id",
        "last_edited_time": "2024-01-15T16:00:00.000Z",
        "title": "New Page"
    })
    
    # Second import
    response2 = await client.post("/pipeline/import-from-notion?page_size=100")
    await wait_for_job(client, response2.json()["job_id"])
    
    status = await client.get(f"/pipeline/import-status/{response2.json()['job_id']}")
    job = status.json()
    
    # Assertions
    assert job["imported_pages"] == 1  # Only new page
    assert job["skipped_pages"] == 726  # Old pages skipped

@pytest.mark.integration
@pytest.mark.asyncio
async def test_import_with_ten_modified_pages(client: AsyncClient, notion_api_mock):
    """10개 페이지 수정 후 재실행"""
    # First import
    response1 = await client.post("/pipeline/import-from-notion?page_size=100")
    await wait_for_job(client, response1.json()["job_id"])
    
    # Simulate: User modifies 10 pages
    for i in range(10):
        notion_api_mock.update_page(
            page_id=f"existing-page-{i}",
            last_edited_time="2024-01-15T17:00:00.000Z"
        )
    
    # Second import
    response2 = await client.post("/pipeline/import-from-notion?page_size=100")
    await wait_for_job(client, response2.json()["job_id"])
    
    status = await client.get(f"/pipeline/import-status/{response2.json()['job_id']}")
    job = status.json()
    
    # Assertions
    assert job["imported_pages"] == 10  # 10 modified
    assert job["skipped_pages"] == 716  # 726 - 10

@pytest.mark.integration
@pytest.mark.asyncio
async def test_rpc_performance(client: AsyncClient):
    """RPC 성능 테스트"""
    import time
    
    # Measure import time with no changes (should be fast)
    response1 = await client.post("/pipeline/import-from-notion?page_size=100")
    await wait_for_job(client, response1.json()["job_id"])
    
    start_time = time.time()
    response2 = await client.post("/pipeline/import-from-notion?page_size=100")
    await wait_for_job(client, response2.json()["job_id"])
    elapsed = time.time() - start_time
    
    # Should complete in < 5 seconds (no content fetching)
    assert elapsed < 5.0, f"Import took {elapsed:.2f}s, expected < 5s"

# Helper function
async def wait_for_job(client: AsyncClient, job_id: str, timeout: int = 600):
    """Wait for job to complete."""
    for _ in range(timeout // 10):
        response = await client.get(f"/pipeline/import-status/{job_id}")
        job = response.json()
        
        if job["status"] in ["completed", "failed"]:
            return job
        
        await asyncio.sleep(10)
    
    pytest.fail(f"Job {job_id} timeout after {timeout}s")
```

---

### ⚠️ 예상 리스크 및 대응

| 리스크 | 확률 | 영향 | 대응 방안 |
|-------|------|------|---------|
| SQL 함수 배포 실패 | 낮음 | 중간 | Fallback 자동 작동, 수동 재배포 |
| RPC 호출 타임아웃 | 낮음 | 낮음 | Fallback to 방식 A |
| UUID 검증 오탐지 | 매우 낮음 | 낮음 | 로그 확인 후 패턴 수정 |
| 성능 목표 미달 (>200ms) | 낮음 | 중간 | SQL 최적화 (JOIN 방식 전환) |
| Notion API 형식 변경 | 매우 낮음 | 높음 | 모니터링 + 긴급 Hotfix |
| DB 연결 끊김 | 낮음 | 높음 | Retry 로직 + 에러 핸들링 |

---

### 🔄 롤백 계획

**긴급 롤백 옵션 1: 환경 변수**
```bash
# .env
USE_RPC_CHANGE_DETECTION=false

# supabase_service.py 수정
if not os.getenv("USE_RPC_CHANGE_DETECTION", "true").lower() == "true":
    logger.warning("RPC disabled by env var")
    return await self._full_scan_fallback(notion_pages)
```

**긴급 롤백 옵션 2: Git revert**
```bash
git revert <commit-hash>
git push origin main
```

**롤백 트리거:**
- RPC 함수 배포 실패하고 Fallback도 작동 안 함
- 성능이 기존보다 느려짐 (>10초)
- 데이터 무결성 문제 발견 (페이지 누락)

---

### ✅ 성공 기준

**기능 요구사항:**
- ✅ 초기 import: 726개 전체 처리
- ✅ 재실행 (변경 없음): 0 imported, 726 skipped
- ✅ 1개 추가: 1 imported, 726 skipped
- ✅ 10개 수정: 10 imported, 716 skipped
- ✅ RPC 실패 시 Fallback 작동

**성능 요구사항:**
- ✅ RPC 응답: < 200ms
- ✅ Fallback 응답: < 300ms
- ✅ 전체 import (변경 없음): < 5초

**안정성 요구사항:**
- ✅ RPC 실패해도 import 완료
- ✅ 모든 에러 로그 기록
- ✅ 데이터 무결성 보장

**테스트 요구사항:**
- ✅ 단위 테스트 10개 통과
- ✅ 통합 테스트 5개 통과
- ✅ 커버리지 > 80%

---

### 📊 구현 시간 예상

| Phase | 작업 | 예상 시간 | 누적 시간 |
|-------|------|----------|----------|
| 1 | SQL 함수 생성 및 배포 | 30분 | 30분 |
| 2 | Python 코드 재구현 | 45분 | 1시간 15분 |
| 3 | Startup 검증 추가 | 15분 | 1시간 30분 |
| 4 | 단위 테스트 작성 | 45분 | 2시간 15분 |
| 5 | 통합 테스트 실행 | 30분 | 2시간 45분 |
| **총계** | | **2시간 45분** | |

**버퍼 포함:** 3시간 (디버깅 15분 추가)

---

## 🎯 최종 확인 사항

구현 시작 전 확인:
- [ ] Supabase 접근 권한 확인
- [ ] SQL Editor 사용 가능 확인
- [ ] 기존 코드 백업 완료
- [ ] 테스트 환경 준비
- [ ] Git 브랜치 생성 (`feature/incremental-import-rpc`)

구현 중 주의:
- [ ] 각 Phase 완료 후 Git commit
- [ ] 테스트 통과 확인 후 다음 Phase 진행
- [ ] 에러 발생 시 로그 캡처
- [ ] 성능 측정 결과 기록

구현 완료 후:
- [ ] 전체 테스트 재실행
- [ ] 성능 벤치마크 기록
- [ ] Pull Request 생성
- [ ] 팀원 리뷰 요청

---

## 🔴 CRITICAL PRE-IMPLEMENTATION VERIFICATION REQUIRED
