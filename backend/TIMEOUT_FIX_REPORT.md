# Import API Timeout Issue - Diagnosis & Fix

## Issue Description
The `/pipeline/import-from-notion` endpoint was timing out with no server logs appearing.

## Root Cause Analysis

### Problem 1: Missing Logging Configuration
- **Symptom:** No logs appeared in server output
- **Cause:** `logging.basicConfig()` was never called in `main.py`
- **Fix:** Added logging configuration with INFO level and StreamHandler

### Problem 2: Sequential Content Fetching Causing Timeout
- **Symptom:** Endpoint hung for 30+ seconds and client timed out
- **Cause:** The endpoint fetches content for ALL 724 child pages sequentially
  - Each page requires 1 API call to Notion
  - Rate limit: 3 req/sec
  - Total time: 724 pages / 3 req/sec = 241 seconds (4 minutes)
- **Location:** `routers/pipeline.py`, lines 83-102
- **Fix (Temporary):** Commented out the content-fetching loop

## Test Results

### Before Fix
- Request timeout after 10+ seconds
- No response from server
- No logs visible

### After Fix
- Successfully imported 724 pages in 36 seconds
- Response: `{"success":true,"imported_count":724,"skipped_count":0,"errors":[]}`
- Logs now visible in console

## Files Modified

1. `/backend/main.py`
   - Added logging configuration (lines 7-14)

2. `/backend/routers/pipeline.py`
   - Commented out content-fetching loop (lines 83-102)
   - Added TODO comment for proper fix

## Temporary Limitation

**IMPORTANT:** The current fix skips fetching page content. This means:
- Child pages are imported with metadata only
- Page content (blocks) is NOT fetched
- The `properties["본문"]` field will be empty or contain only the title

This is acceptable for RAW layer import (Step 1) but will cause issues in Step 2 (extract thoughts) if content is needed.

## Recommended Long-Term Solutions

### Option 1: Background Task (RECOMMENDED)
Use FastAPI BackgroundTasks to process import asynchronously:

```python
from fastapi import BackgroundTasks

@router.post("/import-from-notion")
async def import_from_notion(
    background_tasks: BackgroundTasks,
    ...
):
    job_id = str(uuid.uuid4())
    background_tasks.add_task(process_import, job_id, ...)
    return {"job_id": job_id, "status": "processing"}

@router.get("/import-status/{job_id}")
async def get_import_status(job_id: str):
    # Check Redis/database for job status
    return {"job_id": job_id, "status": "...", "progress": "..."}
```

**Pros:**
- Immediate response to client
- Can track progress
- Follows REST best practices for long-running operations

**Cons:**
- Requires job tracking (Redis or database)
- More complex implementation

### Option 2: Lazy Content Loading
Don't fetch content during import, fetch it only when needed (during thought extraction):

```python
# Step 1: Import only metadata
@router.post("/import-from-notion")
async def import_from_notion(...):
    # Fetch pages without content
    pages = await notion_service.fetch_child_pages_from_parent(...)
    # Store with content=NULL
    
# Step 2: Extract thoughts (fetch content on-demand)
@router.post("/extract-thoughts")
async def extract_thoughts(...):
    for raw_note in raw_notes:
        if raw_note.content is None:
            # Fetch content now
            content = await notion_service.fetch_page_blocks(...)
            await supabase_service.update_raw_note_content(...)
```

**Pros:**
- Faster import
- Content fetched only when actually needed
- Simpler than background tasks

**Cons:**
- Step 2 will take longer
- Content fetching spread across multiple steps

### Option 3: Batch Processing with Pagination
Add pagination to import endpoint:

```python
@router.post("/import-from-notion")
async def import_from_notion(
    page_size: int = 100,
    offset: int = 0,
    fetch_content: bool = False,
    ...
):
    # Fetch only a batch of pages
    pages = await notion_service.fetch_child_pages_from_parent(...)
    pages_batch = pages[offset:offset+page_size]
    
    if fetch_content:
        # Fetch content for this batch only
        ...
```

**Pros:**
- User controls batch size
- Can process incrementally

**Cons:**
- Requires multiple API calls from client
- More complex client-side logic

## Recommendation

Implement **Option 2 (Lazy Content Loading)** as the immediate solution:
1. Keep current fix (skip content during import)
2. Update Step 2 (extract thoughts) to fetch content on-demand
3. This matches the architecture: RAW layer is just pointers, content loaded when needed

Later, if needed, add **Option 1 (Background Tasks)** for better UX with progress tracking.

## Next Steps

1. Test current fix with full pipeline (import → extract → pairs → essays)
2. Update Step 2 to handle NULL content and fetch on-demand
3. Add progress tracking if import takes too long
4. Consider adding a `/health` check that includes database connectivity

