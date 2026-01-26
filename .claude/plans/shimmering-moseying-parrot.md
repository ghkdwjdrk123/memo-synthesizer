# 다음 스텝: MVP 확장 계획

## 현재 상태 (MVP 완성)

✅ **완료된 항목:**
1. 백엔드 파이프라인 4단계 (RAW → NORMALIZED → ZK → Essay)
2. 프론트엔드 브런치 스타일 뷰어 (목록 + 상세)
3. 유사도 버그 수정 (낮은 유사도 우선 선택)
4. 테스트 44개 통과

## 제안된 확장 방향 (2가지)

### 옵션 1: Notion 대량 페이지 수집 확장
**목표:** 수백 개의 하위 페이지를 재귀적으로 가져와 raw_notes 테이블에 저장

### 옵션 2: 다중 Pair ZK 알고리즘
**목표:** 현재 2-pair 기반 → N-pair (3개 이상 thought 조합)로 확장

---

## 옵션 1: Notion 대량 페이지 수집 (Bulk Import)

### 현재 상태 상세 분석

#### 코드 레벨 제약 사항 (라인별 분석)

**1. `backend/services/notion_service.py`**

**현재 구현 (Lines 42-84: `query_database()`):**
```python
Line 53-56: response = self.client.databases.query(
               database_id=self.database_id,
               page_size=page_size
            )  # 단 1회 호출, cursor 파라미터 없음

Line 76:    "has_more": response.get("has_more", False)  # 캡처만 하고 사용 안 함
Line 77:    return {...}  # 여기서 종료, 루프 없음
```

**문제점:**
- `has_more=True`여도 추가 페이지 가져오지 않음
- `next_cursor` 필드를 아예 캡처하지 않음
- `start_cursor` 파라미터를 query()에 전달하지 않음

**2. `backend/routers/pipeline.py`**

**현재 import 로직 (Lines 26-126: `import_from_notion()`):**
```python
Line 28:  page_size: int = Query(default=100, le=100)  # 100개 하드 리미트
Line 54:  pages_data = await notion_service.query_database(page_size=page_size)
Line 70-99: for page in pages_data.get("pages", []):
    Line 91:    content=None  # 하드코딩, 블록 콘텐츠 미수집
    Line 102:   await supabase_service.upsert_raw_note(raw_note)
                # 페이지당 1번 DB 호출 (배치 없음)
```

**문제점:**
- 100개 제한 (Line 28)
- `content` 필드가 항상 `None` (Line 91)
- 페이지별 순차 upsert (N번 DB 호출)
- `blocks.children.list()` API 호출 없음

**3. Rate Limiting 미구현**

**현재 상태:**
- `backend/config.py`에 `RATE_LIMIT_NOTION` 설정 없음
- `rate_limiter.py` 존재하지 않음
- Notion API는 3 req/sec 제한 있으나 코드에 throttling 로직 없음
- 429 에러 발생 시 exponential backoff 없음

**4. 블록 콘텐츠 수집 미구현**

**필요한 API:**
```python
# 현재 코드에 없음
client.blocks.children.list(page_id, page_size=100)
```

**Notion 블록 구조:**
```json
{
  "results": [
    {"type": "paragraph", "paragraph": {"rich_text": [...]}},
    {"type": "heading_1", "heading_1": {"rich_text": [...]}},
    {"type": "bulleted_list_item", ...}
  ],
  "has_more": true,
  "next_cursor": "..."
}
```

**처리 필요 블록 타입:**
- paragraph, heading_1/2/3, bulleted_list_item, numbered_list_item
- quote, callout, toggle (접힌 콘텐츠)
- code (코드 블록), equation (수식)

**5. 재귀 탐색 미구현**

**필요한 시나리오:**
- Database → Page → Child Database → Child Pages
- Page → Child Page → Grandchild Page

**현재 상태:**
- 단일 depth만 지원 (root database의 직접 children만)
- `child_page`, `child_database` 블록 타입 처리 없음

### 구현 계획 (Phase별 상세 설계)

---

## Phase 1: 페이지네이션 루프 구현 (필수, 2-3시간)

### 목표
- 100개 제한 제거
- `has_more=True`일 때 자동으로 다음 배치 가져오기
- 수백~수천 개 페이지 수집 가능

### 구현 상세

#### 1-1. `backend/services/notion_service.py` 수정

**현재 코드 (Lines 42-84):**
```python
async def query_database(self, page_size: int = 10) -> dict:
    response = self.client.databases.query(
        database_id=self.database_id,
        page_size=page_size
    )
    # ... 단일 배치만 처리
    return {"pages": pages, "has_more": response.get("has_more", False)}
```

**새로운 구현:**
```python
async def fetch_all_database_pages(
    self,
    database_id: str | None = None,
    page_size: int = 100
) -> list[dict]:
    """
    데이터베이스의 모든 페이지를 페이지네이션으로 가져오기

    Args:
        database_id: 타겟 데이터베이스 ID (None이면 self.database_id 사용)
        page_size: 배치당 페이지 수 (최대 100)

    Returns:
        List of page objects (Notion API response format)

    Raises:
        NotionAPIError: API 호출 실패 시
    """
    target_db_id = database_id or self.database_id
    all_pages = []
    start_cursor = None
    batch_count = 0

    logger.info(f"Starting pagination for database {target_db_id}")

    while True:
        try:
            # Rate limiting 적용 (Phase 2에서 추가 예정)
            # await self.rate_limiter.acquire()

            # Notion API 호출
            response = self.client.databases.query(
                database_id=target_db_id,
                page_size=page_size,
                **({"start_cursor": start_cursor} if start_cursor else {})
            )

            # 현재 배치 페이지 추가
            batch_pages = response.get("results", [])
            all_pages.extend(batch_pages)
            batch_count += 1

            logger.info(
                f"Batch {batch_count}: Fetched {len(batch_pages)} pages "
                f"(Total: {len(all_pages)})"
            )

            # 다음 배치 확인
            has_more = response.get("has_more", False)
            if not has_more:
                logger.info(f"Pagination complete: {len(all_pages)} total pages")
                break

            # 다음 cursor 설정
            start_cursor = response.get("next_cursor")
            if not start_cursor:
                logger.warning("has_more=True but no next_cursor, stopping")
                break

        except Exception as e:
            logger.error(
                f"Pagination error at batch {batch_count} "
                f"(cursor: {start_cursor}): {e}"
            )
            # Phase 3에서 재시도 로직 추가 예정
            raise

    return all_pages
```

**변경 사항:**
- **Line 42-84 전체 교체** → 새로운 `fetch_all_database_pages()` 메서드
- 기존 `query_database()` → 내부적으로 `fetch_all_database_pages()` 호출하도록 래퍼 유지 (하위 호환성)
- `start_cursor` 파라미터 추가
- `while True` 루프로 `has_more=False`까지 반복

#### 1-2. `backend/routers/pipeline.py` 수정

**현재 코드 (Lines 26-60):**
```python
@router.post("/import-from-notion")
async def import_from_notion(
    page_size: int = Query(default=100, le=100),  # ← 제한
    notion_credentials: NotionCredentials = Depends(get_notion_credentials),
    supabase_service: SupabaseService = Depends(get_supabase_service),
):
    notion_service = NotionService(...)
    pages_data = await notion_service.query_database(page_size=page_size)
    pages = pages_data.get("pages", [])
    # ...
```

**새로운 구현:**
```python
@router.post("/import-from-notion")
async def import_from_notion(
    # page_size 파라미터 제거 (항상 100 사용)
    fetch_all: bool = Query(
        default=True,
        description="True: 모든 페이지 가져오기, False: 첫 100개만"
    ),
    notion_credentials: NotionCredentials = Depends(get_notion_credentials),
    supabase_service: SupabaseService = Depends(get_supabase_service),
):
    logger.info(f"Starting Notion import (fetch_all={fetch_all})")

    notion_service = NotionService(
        api_key=notion_credentials.api_key,
        database_id=notion_credentials.database_id,
    )

    # 새로운 메서드 호출
    if fetch_all:
        pages = await notion_service.fetch_all_database_pages(page_size=100)
    else:
        # 하위 호환: 단일 배치만 (테스트용)
        pages_data = await notion_service.query_database(page_size=100)
        pages = pages_data.get("pages", [])

    logger.info(f"Fetched {len(pages)} pages from Notion")

    # 나머지 로직 동일 (Lines 70-126)
    # ...
```

**변경 사항:**
- **Line 28:** `page_size` 파라미터 제거, `fetch_all` 플래그 추가
- **Line 54:** `fetch_all_database_pages()` 호출
- **Line 50-60:** 조건부로 단일 배치 지원 (하위 호환)

#### 1-3. 테스트 전략

**테스트 데이터:**
- 100개 미만 페이지: 단일 배치 테스트
- 150개 페이지: 2 배치 테스트
- 300개 페이지: 3 배치 테스트

**검증 항목:**
- `start_cursor`가 올바르게 전달되는지
- `has_more=False`까지 루프가 도는지
- 중복 페이지 없는지 (notion_page_id 기준)
- 로그 출력이 배치별로 표시되는지

**Phase 1 완료 기준:**
✅ 100개 이상 페이지를 한 번의 API 호출로 모두 가져올 수 있음
✅ `has_more`, `next_cursor` 로직이 정상 동작
✅ 기존 테스트 44개가 여전히 통과

---

## Phase 2: 블록 콘텐츠 수집 (중요, 3-4시간)

### 목표
- `content` 필드를 `None`이 아닌 실제 페이지 본문으로 채우기
- Notion 블록 API (`blocks.children.list()`) 호출
- 다양한 블록 타입 처리 (paragraph, heading, list, quote 등)

### 구현 상세

#### 2-1. `backend/services/notion_service.py` - 블록 추출 메서드 추가

**새로운 메서드 1: Rich Text 추출 헬퍼**
```python
def _extract_rich_text(self, rich_text_array: list[dict]) -> str:
    """
    Notion rich_text 배열에서 일반 텍스트 추출

    Args:
        rich_text_array: Notion rich_text 객체 리스트

    Returns:
        결합된 plain text 문자열

    Example:
        Input: [{"plain_text": "Hello "}, {"plain_text": "World"}]
        Output: "Hello World"
    """
    if not rich_text_array:
        return ""

    texts = [item.get("plain_text", "") for item in rich_text_array]
    return "".join(texts)
```

**새로운 메서드 2: 블록 콘텐츠 수집 (핵심)**
```python
async def fetch_page_blocks(
    self,
    page_id: str,
    max_depth: int = 2
) -> str:
    """
    페이지의 모든 블록을 가져와 텍스트로 변환

    Args:
        page_id: 타겟 페이지 ID
        max_depth: 중첩 블록 탐색 깊이 (toggle, column 등)

    Returns:
        페이지 전체 텍스트 (마크다운 스타일)

    Raises:
        NotionAPIError: API 호출 실패 시
    """
    content_parts = []
    start_cursor = None

    logger.debug(f"Fetching blocks for page {page_id}")

    while True:
        try:
            # Rate limiting 적용 (Phase 3에서 구현)
            # await self.rate_limiter.acquire()

            # 블록 목록 가져오기 (페이지네이션 지원)
            response = self.client.blocks.children.list(
                block_id=page_id,
                page_size=100,
                **({"start_cursor": start_cursor} if start_cursor else {})
            )

            blocks = response.get("results", [])

            # 각 블록 처리
            for block in blocks:
                block_type = block.get("type")
                block_data = block.get(block_type, {})

                # 블록 타입별 텍스트 추출
                if block_type == "paragraph":
                    text = self._extract_rich_text(block_data.get("rich_text", []))
                    if text.strip():
                        content_parts.append(text)

                elif block_type in ["heading_1", "heading_2", "heading_3"]:
                    text = self._extract_rich_text(block_data.get("rich_text", []))
                    if text.strip():
                        # 마크다운 스타일 헤더
                        level = int(block_type[-1])
                        content_parts.append(f"{'#' * level} {text}")

                elif block_type in ["bulleted_list_item", "numbered_list_item"]:
                    text = self._extract_rich_text(block_data.get("rich_text", []))
                    if text.strip():
                        prefix = "-" if block_type == "bulleted_list_item" else "1."
                        content_parts.append(f"{prefix} {text}")

                elif block_type == "quote":
                    text = self._extract_rich_text(block_data.get("rich_text", []))
                    if text.strip():
                        content_parts.append(f"> {text}")

                elif block_type == "callout":
                    text = self._extract_rich_text(block_data.get("rich_text", []))
                    emoji = block_data.get("icon", {}).get("emoji", "💡")
                    if text.strip():
                        content_parts.append(f"{emoji} {text}")

                elif block_type == "code":
                    text = self._extract_rich_text(block_data.get("rich_text", []))
                    language = block_data.get("language", "")
                    if text.strip():
                        content_parts.append(f"```{language}\n{text}\n```")

                elif block_type == "toggle":
                    # 토글 제목만 추출 (중첩 블록은 max_depth 제어)
                    text = self._extract_rich_text(block_data.get("rich_text", []))
                    if text.strip():
                        content_parts.append(f"▶ {text}")

                # TODO: 추가 블록 타입 (table, image 등) Phase 2.5에서 구현

            # 페이지네이션 체크
            has_more = response.get("has_more", False)
            if not has_more:
                break

            start_cursor = response.get("next_cursor")

        except Exception as e:
            logger.error(f"Error fetching blocks for page {page_id}: {e}")
            # 블록 가져오기 실패해도 부분적으로 수집된 콘텐츠 반환
            break

    # 텍스트 결합 (각 블록 사이 빈 줄)
    full_content = "\n\n".join(content_parts)
    logger.debug(f"Extracted {len(full_content)} characters from page {page_id}")

    return full_content
```

**변경 사항:**
- **새로운 메서드 추가** (기존 코드 수정 없음)
- `_extract_rich_text()` - Line 125 이후 추가
- `fetch_page_blocks()` - Line 200 이후 추가
- 9가지 블록 타입 지원 (paragraph, heading 1/2/3, list, quote, callout, code, toggle)

#### 2-2. `backend/routers/pipeline.py` - content 필드 채우기

**현재 코드 (Lines 70-99):**
```python
for page in pages:
    try:
        page_id = page.get("id")
        # ...
        content = None  # ← 여기가 문제
```

**새로운 구현:**
```python
for page in pages:
    try:
        page_id = page.get("id")

        # 제목 추출 (기존 로직)
        properties = page.get("properties", {})
        title = None
        for key in ["제목", "Name", "이름", "title"]:
            if key in properties:
                title_data = properties[key]
                if title_data.get("type") == "title":
                    title_array = title_data.get("title", [])
                    if title_array:
                        title = title_array[0].get("plain_text", "")
                        break

        # ✨ 새로운 부분: 블록 콘텐츠 가져오기
        try:
            content = await notion_service.fetch_page_blocks(page_id)

            # 콘텐츠 없는 빈 페이지 처리
            if not content or len(content.strip()) < 10:
                logger.warning(f"Page {page_id} has no meaningful content")
                content = None  # thought 추출 시 스킵됨

        except Exception as e:
            logger.warning(f"Failed to fetch blocks for page {page_id}: {e}")
            content = None  # 블록 가져오기 실패해도 페이지는 저장

        # 나머지 로직 동일
        notion_url = f"https://www.notion.so/{page_id.replace('-', '')}"
        # ...
```

**변경 사항:**
- **Line 85-95** 영역에 블록 콘텐츠 수집 로직 추가
- `content=None` → `content=await notion_service.fetch_page_blocks(page_id)`
- Try-except로 블록 가져오기 실패 시에도 페이지는 저장 (content만 None)
- 10자 미만 짧은 콘텐츠는 `None` 처리 (thought 추출 단계에서 필터링됨)

#### 2-3. 성능 최적화 고려 사항

**문제:** 페이지 300개 × 블록 API 호출 1회 = 300번 추가 API 호출

**최적화 전략 (Phase 2.5에서 선택적 구현):**

**옵션 A: 배치 처리**
```python
# 10개씩 배치로 블록 가져오기
async def fetch_blocks_batch(page_ids: list[str]) -> dict[str, str]:
    tasks = [fetch_page_blocks(page_id) for page_id in page_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return dict(zip(page_ids, results))
```

**옵션 B: 콘텐츠 수집 분리 (추천)**
```python
# Step 1: 메타데이터만 먼저 import (빠름)
# Step 2: 백그라운드 작업으로 콘텐츠 채우기 (별도 엔드포인트)
POST /pipeline/fetch-content?page_ids=...
```

**Phase 2에서는 순차 처리로 구현, Phase 2.5에서 배치 최적화 검토**

#### 2-4. 테스트 전략

**테스트 케이스:**
1. **빈 페이지** - 블록 0개 → `content=None`
2. **단순 페이지** - paragraph 5개 → 정상 텍스트 추출
3. **복합 페이지** - heading + list + quote → 마크다운 형식 확인
4. **대량 블록** - 100개 이상 블록 → 페이지네이션 확인
5. **에러 페이지** - 블록 API 실패 → `content=None`, 페이지는 저장됨

**검증 항목:**
- `raw_notes.content` 필드가 `NULL`이 아닌 실제 텍스트로 채워짐
- Step 2 (thought 추출)에서 `content`를 제대로 읽어옴
- 블록 타입별로 올바르게 포맷팅됨

**Phase 2 완료 기준:**
✅ `content` 필드가 실제 페이지 본문으로 채워짐
✅ 9가지 주요 블록 타입 처리 가능
✅ 블록 가져오기 실패해도 import 프로세스는 계속 진행

## Phase 3: Rate Limiting 구현 (필수, 2시간)

### 목표
- Notion API 3 req/sec 제한 준수
- 429 Too Many Requests 에러 방지
- Exponential backoff로 재시도

### 구현 상세

#### 3-1. `backend/services/rate_limiter.py` 생성 (신규 파일)

```python
"""
Rate Limiter for API calls using Token Bucket algorithm
"""
import time
import asyncio
from typing import Optional


class RateLimiter:
    """
    Token Bucket 기반 Rate Limiter

    Usage:
        limiter = RateLimiter(rate=3.0)  # 3 req/sec
        await limiter.acquire()
    """

    def __init__(self, rate: float = 3.0):
        """
        Args:
            rate: 초당 허용 요청 수 (예: 3.0 = 3 req/sec)
        """
        self.rate = rate
        self.tokens = rate
        self.max_tokens = rate
        self.last_update = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self):
        """
        토큰을 소비하고 요청 실행 권한 획득
        토큰이 부족하면 대기
        """
        async with self.lock:
            while self.tokens < 1:
                # 토큰 보충
                now = time.monotonic()
                elapsed = now - self.last_update
                self.tokens = min(
                    self.max_tokens,
                    self.tokens + elapsed * self.rate
                )
                self.last_update = now

                if self.tokens < 1:
                    # 대기 시간 계산
                    sleep_time = (1 - self.tokens) / self.rate
                    await asyncio.sleep(sleep_time)

            # 토큰 1개 소비
            self.tokens -= 1


class ExponentialBackoff:
    """
    Exponential Backoff 재시도 전략

    Usage:
        backoff = ExponentialBackoff()
        for attempt in range(max_retries):
            try:
                result = await api_call()
                break
            except Exception as e:
                await backoff.sleep(attempt)
    """

    def __init__(
        self,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        multiplier: float = 2.0
    ):
        """
        Args:
            base_delay: 초기 대기 시간 (초)
            max_delay: 최대 대기 시간 (초)
            multiplier: 지수 배율
        """
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.multiplier = multiplier

    async def sleep(self, attempt: int):
        """
        재시도 attempt에 따라 대기

        Args:
            attempt: 재시도 횟수 (0부터 시작)
        """
        delay = min(
            self.base_delay * (self.multiplier ** attempt),
            self.max_delay
        )
        await asyncio.sleep(delay)
```

#### 3-2. `backend/config.py` 수정

**추가할 설정:**
```python
# Line 40 이후 추가

# Rate Limiting
RATE_LIMIT_NOTION: int = Field(default=3, env="RATE_LIMIT_NOTION")
RATE_LIMIT_OPENAI: int = Field(default=10, env="RATE_LIMIT_OPENAI")
RATE_LIMIT_ANTHROPIC: int = Field(default=5, env="RATE_LIMIT_ANTHROPIC")

# Retry Configuration
MAX_RETRIES: int = Field(default=3, env="MAX_RETRIES")
RETRY_BASE_DELAY: float = Field(default=1.0, env="RETRY_BASE_DELAY")
RETRY_MAX_DELAY: float = Field(default=60.0, env="RETRY_MAX_DELAY")
```

#### 3-3. `backend/services/notion_service.py` 수정

**__init__ 메서드 수정 (Lines 13-16):**
```python
from .rate_limiter import RateLimiter, ExponentialBackoff
from ..config import settings

def __init__(self, api_key: str, database_id: str):
    self.client = Client(auth=api_key)
    self.database_id = database_id

    # Rate Limiter 초기화 (NEW)
    self.rate_limiter = RateLimiter(rate=float(settings.RATE_LIMIT_NOTION))
    self.backoff = ExponentialBackoff(
        base_delay=settings.RETRY_BASE_DELAY,
        max_delay=settings.RETRY_MAX_DELAY
    )
```

**API 호출 부분 수정 (Lines 160-170, 345-355):**
```python
# fetch_all_database_pages() 내부 (Line 162 주석 해제)
await self.rate_limiter.acquire()

response = self.client.databases.query(...)

# fetch_page_blocks() 내부 (Line 347 주석 해제)
await self.rate_limiter.acquire()

response = self.client.blocks.children.list(...)
```

**429 에러 재시도 로직 추가:**
```python
# fetch_all_database_pages() try-except 수정 (Lines 160-200)
for retry in range(settings.MAX_RETRIES):
    try:
        await self.rate_limiter.acquire()

        response = self.client.databases.query(...)
        break  # 성공 시 루프 탈출

    except APIResponseError as e:
        if e.code == 429:  # Too Many Requests
            logger.warning(f"Rate limited, retrying ({retry+1}/{settings.MAX_RETRIES})")
            await self.backoff.sleep(retry)
            continue
        else:
            raise  # 다른 에러는 즉시 raise

    except Exception as e:
        logger.error(f"API error: {e}")
        if retry < settings.MAX_RETRIES - 1:
            await self.backoff.sleep(retry)
            continue
        raise
```

#### 3-4. 테스트 전략

**Rate Limiting 검증:**
1. **수동 테스트** - 로그로 요청 간격 확인
   ```
   [INFO] Request 1 at 0.00s
   [INFO] Request 2 at 0.33s  # ← 1/3초 간격
   [INFO] Request 3 at 0.66s
   ```

2. **429 에러 시뮬레이션** - Mock API로 429 반환 → 재시도 확인

**Phase 3 완료 기준:**
✅ Notion API 호출이 3 req/sec 속도로 throttle됨
✅ 429 에러 발생 시 exponential backoff로 재시도
✅ 로그에서 rate limiting 동작 확인 가능

---

## Phase 4: 하위 페이지 재귀 탐색 (선택, 3-4시간)

### 목표
- Database 내 child_page, child_database 블록 탐색
- 최대 depth 제어 (무한 재귀 방지)
- 페이지 계층 구조 flat하게 저장

### 구현 상세

#### 4-1. `backend/services/notion_service.py` - 재귀 메서드 추가

```python
async def fetch_child_pages_recursive(
    self,
    parent_id: str,
    max_depth: int = 3,
    current_depth: int = 0
) -> list[dict]:
    """
    재귀적으로 하위 페이지 탐색

    Args:
        parent_id: 부모 페이지/데이터베이스 ID
        max_depth: 최대 탐색 깊이
        current_depth: 현재 깊이 (내부 사용)

    Returns:
        List of page objects (flat list, 계층 구조 유지 안 함)

    Note:
        child_database는 database로 query
        child_page는 page로 retrieve
    """
    if current_depth >= max_depth:
        logger.debug(f"Max depth {max_depth} reached for {parent_id}")
        return []

    child_pages = []

    try:
        await self.rate_limiter.acquire()

        # 블록 목록 가져오기
        blocks_response = self.client.blocks.children.list(
            block_id=parent_id,
            page_size=100
        )

        for block in blocks_response.get("results", []):
            block_type = block.get("type")
            block_id = block.get("id")

            if block_type == "child_page":
                # Child page는 pages.retrieve()로 상세 정보 가져오기
                await self.rate_limiter.acquire()
                page_data = self.client.pages.retrieve(page_id=block_id)
                child_pages.append(page_data)

                # 재귀 호출
                grandchildren = await self.fetch_child_pages_recursive(
                    block_id,
                    max_depth,
                    current_depth + 1
                )
                child_pages.extend(grandchildren)

            elif block_type == "child_database":
                # Child database는 databases.query()로 페이지들 가져오기
                db_pages = await self.fetch_all_database_pages(
                    database_id=block_id,
                    page_size=100
                )
                child_pages.extend(db_pages)

                # 각 페이지의 하위 페이지도 탐색
                for db_page in db_pages:
                    grandchildren = await self.fetch_child_pages_recursive(
                        db_page.get("id"),
                        max_depth,
                        current_depth + 1
                    )
                    child_pages.extend(grandchildren)

    except Exception as e:
        logger.error(f"Error fetching children of {parent_id}: {e}")
        # 에러 발생해도 이미 수집한 페이지들은 반환

    return child_pages
```

#### 4-2. `backend/routers/pipeline.py` 수정

**파라미터 추가:**
```python
@router.post("/import-from-notion")
async def import_from_notion(
    fetch_all: bool = Query(default=True),
    include_children: bool = Query(
        default=False,
        description="True: 하위 페이지도 재귀적으로 가져오기"
    ),
    max_depth: int = Query(
        default=3,
        ge=1,
        le=5,
        description="하위 페이지 탐색 최대 깊이 (1-5)"
    ),
    # ...
):
    # ...

    # 루트 데이터베이스 페이지 가져오기
    root_pages = await notion_service.fetch_all_database_pages(page_size=100)

    all_pages = root_pages.copy()

    # 하위 페이지 재귀 탐색 (옵션)
    if include_children:
        logger.info(f"Fetching child pages (max_depth={max_depth})")

        for root_page in root_pages:
            child_pages = await notion_service.fetch_child_pages_recursive(
                parent_id=root_page.get("id"),
                max_depth=max_depth
            )
            all_pages.extend(child_pages)

        logger.info(
            f"Collected {len(root_pages)} root pages + "
            f"{len(all_pages) - len(root_pages)} child pages"
        )

    # 나머지 import 로직 동일 (Lines 70-126)
    # all_pages를 순회하며 처리
```

#### 4-3. 중복 제거 로직

**문제:** 재귀 탐색 시 같은 페이지가 여러 경로로 참조될 수 있음

**해결:**
```python
# pipeline.py 내부 (Line 70 이전)
# 중복 제거 (notion_page_id 기준)
unique_pages = {}
for page in all_pages:
    page_id = page.get("id")
    if page_id not in unique_pages:
        unique_pages[page_id] = page

all_pages = list(unique_pages.values())
logger.info(f"After deduplication: {len(all_pages)} unique pages")
```

#### 4-4. 테스트 전략

**테스트 시나리오:**
1. **단일 depth** - Root 페이지만 (max_depth=0 equivalent)
2. **2-depth** - Root → Child pages
3. **3-depth** - Root → Child → Grandchild
4. **순환 참조** - Page A → Page B → Page A (무한 루프 방지 확인)
5. **대량 하위 페이지** - 각 페이지마다 10개 child → 100페이지 수집 확인

**검증 항목:**
- `max_depth` 제한이 정확히 동작하는지
- 중복 페이지가 없는지
- 순환 참조로 인한 무한 루프가 없는지

**Phase 4 완료 기준:**
✅ `include_children=true`일 때 하위 페이지 재귀 탐색
✅ `max_depth` 파라미터로 깊이 제어
✅ 중복 제거 로직 동작

**Note:** Phase 4는 선택 사항이므로 Phase 1-3 완료 후 필요성 재검토

---

## Phase 5: 배치 처리 & 최적화 (선택, 2시간)

### 목표
- Supabase upsert를 배치로 처리 (N번 → 1번 DB 호출)
- 블록 콘텐츠 수집을 병렬화 (asyncio.gather)

### 구현 상세

#### 5-1. `backend/services/supabase_service.py` - 배치 upsert 추가

```python
async def upsert_raw_notes_batch(
    self,
    notes: list[RawNoteCreate]
) -> dict:
    """
    여러 raw_notes를 한 번에 upsert

    Args:
        notes: RawNoteCreate 리스트

    Returns:
        {"inserted": int, "updated": int}
    """
    if not notes:
        return {"inserted": 0, "updated": 0}

    # Pydantic 모델 → dict 변환
    notes_data = [note.model_dump() for note in notes]

    try:
        response = (
            self.client.table("raw_notes")
            .upsert(
                notes_data,
                on_conflict="notion_page_id",  # 중복 시 업데이트
                count="exact"
            )
            .execute()
        )

        count = response.count or len(notes)
        logger.info(f"Batch upserted {count} raw_notes")

        return {"inserted": count, "updated": 0}  # Supabase는 구분 안 됨

    except Exception as e:
        logger.error(f"Batch upsert failed: {e}")
        raise
```

#### 5-2. `backend/routers/pipeline.py` - 배치 처리로 변경

**현재 (Lines 70-110):**
```python
for page in pages:
    # ... 페이지 처리
    await supabase_service.upsert_raw_note(raw_note)  # N번 호출
```

**변경 후:**
```python
# 모든 페이지를 먼저 처리하고 모아두기
raw_notes_batch = []

for page in pages:
    try:
        # ... 페이지 처리 (기존 로직)
        raw_note = RawNoteCreate(...)
        raw_notes_batch.append(raw_note)

    except Exception as e:
        logger.warning(f"Failed to process page {page.get('id')}: {e}")
        skipped_count += 1
        continue

# 배치로 한 번에 upsert
if raw_notes_batch:
    await supabase_service.upsert_raw_notes_batch(raw_notes_batch)
    imported_count = len(raw_notes_batch)
```

**성능 개선:**
- Before: 300 페이지 → 300번 DB 호출 → ~30초
- After: 300 페이지 → 1번 DB 호출 → ~3초

#### 5-3. 블록 콘텐츠 병렬 수집

**현재 (순차 처리):**
```python
for page in pages:
    content = await notion_service.fetch_page_blocks(page_id)  # 순차
```

**변경 후 (병렬 처리):**
```python
# 10개씩 배치로 병렬 수집
BATCH_SIZE = 10

for i in range(0, len(pages), BATCH_SIZE):
    batch = pages[i:i+BATCH_SIZE]

    # asyncio.gather로 병렬 실행
    tasks = [
        notion_service.fetch_page_blocks(page.get("id"))
        for page in batch
    ]

    contents = await asyncio.gather(*tasks, return_exceptions=True)

    # 결과 매핑
    for page, content in zip(batch, contents):
        if isinstance(content, Exception):
            logger.warning(f"Failed to fetch blocks: {content}")
            content = None

        # content를 페이지에 연결
        page["_content"] = content
```

**성능 개선:**
- Before: 300 페이지 × 0.5초/블록 = 150초 (2.5분)
- After: 300 페이지 / 10 배치 × 0.5초 = 15초

**Trade-off:** Rate limiting과 충돌 가능 → 배치 크기 조정 필요

#### 5-4. 테스트 전략

**성능 벤치마크:**
- 100 페이지 import 시간 측정 (Before / After)
- DB 호출 횟수 카운트 (로그 분석)

**Phase 5 완료 기준:**
✅ Supabase upsert를 배치로 처리
✅ 블록 콘텐츠 수집을 병렬화 (선택)
✅ 전체 import 시간 50% 이상 단축

**Note:** Phase 5는 선택 사항이므로 Phase 1-3 안정화 후 검토

## 구현 우선순위 및 소요 시간

### 권장 순서

**Phase 1 (필수, 2-3시간):**
- 페이지네이션 루프 구현
- 기본 기능 완성 (100개 이상 페이지 수집)

**Phase 2 (필수, 3-4시간):**
- 블록 콘텐츠 수집
- `content` 필드 채우기

**Phase 3 (필수, 2시간):**
- Rate Limiting 구현
- 429 에러 대응

**Phase 4 (선택, 3-4시간):**
- 하위 페이지 재귀 탐색
- Phase 1-3 안정화 후 검토

**Phase 5 (선택, 2시간):**
- 배치 처리 최적화
- 성능 개선

**총 소요 시간 예상:**
- 필수 Phase (1-3): 7-9시간
- 선택 Phase (4-5): +5-6시간
- **합계: 12-15시간** (2-3일)

### 수정/생성 파일 요약

**수정 필요:**
1. `backend/services/notion_service.py`
   - `fetch_all_database_pages()` 추가 (Phase 1)
   - `fetch_page_blocks()` 추가 (Phase 2)
   - `fetch_child_pages_recursive()` 추가 (Phase 4)
   - Rate Limiter 통합 (Phase 3)

2. `backend/routers/pipeline.py`
   - `fetch_all` 파라미터 추가 (Phase 1)
   - 블록 콘텐츠 수집 로직 (Phase 2)
   - `include_children` 파라미터 (Phase 4)
   - 배치 upsert로 변경 (Phase 5)

3. `backend/config.py`
   - Rate limiting 설정 추가 (Phase 3)

4. `backend/services/supabase_service.py`
   - `upsert_raw_notes_batch()` 추가 (Phase 5)

**신규 생성:**
1. `backend/services/rate_limiter.py` (Phase 3)
   - RateLimiter 클래스
   - ExponentialBackoff 클래스

---

## 추가 고려 사항 및 방어 전략

### 1. 중간 에러 재시도 대책

**문제:** 300개 페이지 import 중 150번째에서 오류 발생 시 처음부터 재시작?

**해결 방안 (Phase 2.5, 선택):**

체크포인트 기반 재개:
```python
# 배치마다 cursor 저장
checkpoint = {"last_cursor": next_cursor, "processed": page_count}
save_checkpoint(checkpoint)  # JSON 파일로 저장

# 재시작 시 로드
checkpoint = load_checkpoint()
start_cursor = checkpoint.get("last_cursor")
```

**Phase 1-3에서는:**
- 단순 구현 (체크포인트 없음)
- 실패 시 전체 재시도
- 3-5분 내 완료 가능하므로 허용 가능

---

### 2. 중복 메모 방어 전략

#### 이미 구현된 보호 (DB 레벨)

```sql
-- raw_notes 테이블
notion_page_id TEXT UNIQUE NOT NULL  -- ← UNIQUE 제약
```

```python
# supabase_service.py
.upsert(note_data, on_conflict="notion_page_id")  # ← 중복 시 UPDATE
```

**동작:**
- 같은 페이지 재import → 에러 없이 **업데이트** (덮어쓰기)
- `notion_last_edited_time` 최신화됨

#### 추가 방어 (Phase 1에 추가)

**메모리 내 중복 제거:**
```python
# pipeline.py - import_from_notion()
all_pages = await notion_service.fetch_all_database_pages()

# 중복 제거 (같은 세션 내)
unique_pages = {}
for page in all_pages:
    page_id = page.get("id")
    if page_id not in unique_pages:
        unique_pages[page_id] = page

all_pages = list(unique_pages.values())
logger.info(f"{len(all_pages)} unique pages after deduplication")
```

**Phase 4 재귀 탐색 시:**
- 같은 페이지가 여러 경로로 발견될 수 있음
- 위 로직으로 자동 제거

---

### 3. raw_notes 테이블 스키마 호환성

#### 현재 스키마 (supabase_setup.sql)
```sql
notion_page_id TEXT UNIQUE NOT NULL,
notion_url TEXT NOT NULL,
title TEXT,                              -- nullable
content TEXT,                            -- nullable
properties_json JSONB DEFAULT '{}'::jsonb,
notion_created_time TIMESTAMPTZ NOT NULL,
notion_last_edited_time TIMESTAMPTZ NOT NULL,
imported_at TIMESTAMPTZ DEFAULT NOW()
```

#### Notion API → DB 매핑

| 필드 | Notion API | 현재 로직 | Phase 변경 |
|------|-----------|----------|-----------|
| `notion_page_id` | `page["id"]` | ✅ 매핑됨 | 변경 없음 |
| `notion_url` | N/A | ✅ 수동 생성 | 변경 없음 |
| `title` | `properties["제목/Name"]` | ✅ 추출 | 변경 없음 |
| `content` | N/A | ❌ `None` | **Phase 2: 블록 수집** |
| `properties_json` | `page["properties"]` | ✅ 매핑됨 | 변경 없음 |
| 타임스탬프 | `created_time` | ✅ ISO 파싱 | 변경 없음 |

**호환성:**
- ✅ 기존 스키마와 완전 호환
- ✅ Phase 2에서 `content`만 `None → 텍스트`로 변경
- ✅ nullable이므로 Phase 1에서도 동작

---

### 4. 기타 간과한 지점

#### 4-1. 빈 title 처리

**현재:** `title = None` (nullable) ✅

**추가 방어 (선택):**
```python
if not title:
    title = f"Untitled ({datetime.now().strftime('%Y-%m-%d')})"
```

#### 4-2. 대용량 content 처리

**DB 제약:** `TEXT` 타입 = 최대 1GB ✅

**방어 (Phase 2):**
```python
MAX_CONTENT_LENGTH = 1_000_000  # 1MB

if len(full_content) > MAX_CONTENT_LENGTH:
    logger.warning(f"Content truncated: {len(full_content)} bytes")
    full_content = full_content[:MAX_CONTENT_LENGTH] + "\n[...truncated]"
```

#### 4-3. 미지원 블록 타입

**Phase 2 지원:** paragraph, heading, list, quote, callout, code, toggle

**미지원:** table, image, video, file, bookmark

**처리:**
```python
elif block_type in ["table", "image", "video"]:
    content_parts.append(f"[{block_type.upper()}]")  # 타입만 표시
```

#### 4-4. Notion API 타임존

**현재 코드:**
```python
datetime.fromisoformat(page.get("created_time").replace("Z", "+00:00"))
```
✅ 올바름 (Notion은 UTC로 반환)

#### 4-5. Rate Limiting 정확도

**검증 필요 (Phase 3 테스트):**
```python
# 10번 요청 시 3.33초 소요 확인
for i in range(10):
    await limiter.acquire()
    # 0.33초 간격으로 실행되어야 함
```

---

## 검증 체크리스트

### Phase 1 완료 기준
✅ 100개 이상 페이지 수집
✅ `has_more`, `next_cursor` 정상 동작
✅ 중복 페이지 자동 제거
✅ 기존 테스트 44개 통과

### Phase 2 완료 기준
✅ `content` 필드 실제 텍스트로 채워짐
✅ 9가지 블록 타입 처리
✅ 빈 페이지 → `content=None`
✅ 블록 페이지네이션 동작

### Phase 3 완료 기준
✅ Rate limiting 3 req/sec 준수
✅ 429 에러 재시도
✅ 로그에서 throttling 확인

### 통합 테스트
- **50개 페이지:** 5분 내 완료, content 채워짐
- **300개 페이지:** 20분 내 완료, 중복 0개
- **재import:** 기존 페이지 업데이트 (에러 없음)

---

## 옵션 2: 다중 Pair ZK 알고리즘 (N-Pair)

### 현재 제약 사항

**분석 결과 요약:**
1. **DB 스키마:** `thought_pairs` 테이블이 2개 컬럼만 지원
2. **조합 알고리즘:** C(n,2) 하드코딩 (Stored Procedure JOIN)
3. **LLM 프롬프트:** "두 아이디어" 비교 전제
4. **Essay 생성:** 정확히 2개 thought만 처리
5. **복잡도 폭발:** C(100,3) = 161,700 (32배 증가)

### 필요한 구현 사항

#### 1. DB 스키마 재설계 (필수)

**Option A: Junction 테이블 (권장)**
```sql
-- N개 thought 조합을 저장
CREATE TABLE thought_clusters (
    id SERIAL PRIMARY KEY,
    avg_similarity FLOAT NOT NULL,
    connection_reason TEXT,
    selected_at TIMESTAMPTZ DEFAULT NOW(),
    is_used_in_essay BOOLEAN DEFAULT FALSE
);

CREATE TABLE cluster_thoughts (
    cluster_id INT REFERENCES thought_clusters(id) ON DELETE CASCADE,
    thought_id INT REFERENCES thought_units(id) ON DELETE CASCADE,
    position INT NOT NULL,  -- 순서 보존
    PRIMARY KEY (cluster_id, thought_id)
);

-- essays 테이블 수정
ALTER TABLE essays
    DROP COLUMN pair_id,
    ADD COLUMN cluster_id INT REFERENCES thought_clusters(id);
```

**Option B: JSONB 배열 (간단하지만 덜 유연)**
```sql
CREATE TABLE thought_clusters (
    id SERIAL PRIMARY KEY,
    thought_ids INT[] NOT NULL,  -- [1, 3, 7]
    similarity_matrix JSONB,  -- {"1-3": 0.12, "1-7": 0.25}
    connection_reason TEXT,
    CHECK (array_length(thought_ids, 1) >= 2)
);
```

**마이그레이션 스크립트:**
```sql
-- 기존 thought_pairs 데이터를 thought_clusters로 변환
INSERT INTO thought_clusters (id, avg_similarity, connection_reason, selected_at, is_used_in_essay)
SELECT id, similarity_score, connection_reason, selected_at, is_used_in_essay
FROM thought_pairs;

INSERT INTO cluster_thoughts (cluster_id, thought_id, position)
SELECT id, thought_a_id, 1 FROM thought_pairs
UNION ALL
SELECT id, thought_b_id, 2 FROM thought_pairs;

-- 기존 테이블은 백업 후 삭제
ALTER TABLE thought_pairs RENAME TO thought_pairs_backup;
```

**수정 파일:**
- `/backend/docs/supabase_setup.sql` (새 스키마)
- `/backend/schemas/zk.py` (ThoughtCluster 모델)
- Migration script 생성

---

#### 2. 조합 알고리즘 구현 (필수)

**Python으로 이동 (Stored Procedure 대체)**
```python
# backend/services/supabase_service.py
from itertools import combinations

async def find_candidate_clusters(
    cluster_size: int = 3,
    min_similarity: float = 0.05,
    max_similarity: float = 0.35,
    max_candidates: int = 1000  # 복잡도 제한
) -> List[dict]:
    """N개 thought 조합 생성 (C(n,k))"""

    # 1. 모든 thought_units 조회
    thoughts = await self.get_all_thoughts()

    # 2. C(n, k) 조합 생성
    all_combos = combinations(thoughts, cluster_size)

    # 3. 각 조합의 평균 유사도 계산
    candidates = []
    for combo in all_combos:
        avg_sim = calculate_avg_pairwise_similarity(combo)

        if min_similarity <= avg_sim <= max_similarity:
            candidates.append({
                "thought_ids": [t["id"] for t in combo],
                "thoughts": combo,
                "avg_similarity": avg_sim
            })

        if len(candidates) >= max_candidates:
            break  # 조기 종료

    return candidates
```

**하이브리드 전략 (복잡도 완화, 권장):**
```python
async def find_clusters_hybrid(cluster_size: int = 3):
    """2-pair 기반 점진적 확장"""

    # Step 1: 기존 2-pair 알고리즘으로 좋은 페어 선택
    top_pairs = await find_candidate_pairs(top_n=50, min_score=75)

    # Step 2: 각 페어에 3번째 thought 추가
    clusters = []
    for pair in top_pairs:
        # 페어와 약한 연결된 3번째 thought 찾기
        third = await find_complementary_thought(
            pair.thought_a_id,
            pair.thought_b_id,
            min_sim=0.05,
            max_sim=0.35
        )

        if third:
            clusters.append({
                "thought_ids": [pair.thought_a_id, pair.thought_b_id, third["id"]],
                "avg_similarity": (pair.similarity + third["sim_to_pair"]) / 2
            })

    return clusters
```

**수정 파일:**
- `/backend/services/supabase_service.py` - 조합 알고리즘
- `/backend/routers/pipeline.py` - Step 3 엔드포인트 (cluster_size 파라미터 추가)

---

#### 3. LLM 프롬프트 재작성 (필수)

**현재 (2-pair):**
```
두 아이디어의 창의적 연결 가능성을 평가하세요.

claim_a: ...
claim_b: ...

점수 (0-100):
```

**N-pair 프롬프트:**
```python
# backend/services/ai_service.py
async def score_clusters(clusters: List[dict]) -> List[dict]:
    """N개 thought 조합 평가"""

    prompt = f"""
다음 {cluster_size}개 아이디어의 창의적 조합 가능성을 평가하세요.

{format_thoughts_list(cluster["thoughts"])}

평가 기준:
1. 모든 아이디어가 유기적으로 연결되는가? (30점)
2. 2개 조합보다 {cluster_size}개 조합이 더 풍부한 통찰을 주는가? (40점)
3. 서로 다른 맥락의 아이디어가 교차하는가? (30점)

JSON 형식으로 답변:
{{
  "score": 0-100,
  "reason": "평가 이유 (한글, 100자 이내)"
}}
"""
```

**Essay 생성 프롬프트:**
```python
async def generate_essay_from_cluster(cluster: dict) -> dict:
    """N개 thought로 Essay 생성"""

    prompt = f"""
다음 {len(cluster['thoughts'])}개 사고 단위를 바탕으로 글감을 생성하세요.

{format_thoughts_detailed(cluster['thoughts'])}

요구사항:
- 제목: 5-100자
- outline: {len(cluster['thoughts'])}개 항목 (각 thought당 1개)
  - 1단: 첫 번째 + 두 번째 아이디어 도입
  - 2단: 세 번째 아이디어로 복잡도 확장
  - 3단: 모든 아이디어를 통합한 새로운 통찰
- reason: 왜 이 {len(cluster['thoughts'])}개 조합이 좋은 글감인지 (300자 이내)
"""
```

**수정 파일:**
- `/backend/services/ai_service.py` - score_pairs → score_clusters
- `/backend/services/ai_service.py` - generate_essay 프롬프트 동적화

---

#### 4. 복잡도 완화 전략 (중요)

**문제:**
```
C(100, 2) = 4,950
C(100, 3) = 161,700 (32배)
C(100, 4) = 3,921,225 (790배)
```

**해결책:**

**전략 1: Pre-filtering (유사도 범위)**
```python
# Step 1: 유사도 범위로 먼저 필터링
thoughts_in_range = await filter_by_similarity(min=0.05, max=0.35)
# 100개 → 50개로 축소
# C(50, 3) = 19,600 (manageable)
```

**전략 2: Greedy 확장 (하이브리드)**
```python
# Step 1: 좋은 2-pair 선택 (50개)
pairs = await select_pairs(top_n=50)

# Step 2: 각 페어에 3번째만 추가
# 50 pairs * 50 candidates = 2,500 조합 (vs 161,700)
```

**전략 3: K-means Clustering + Sampling**
```python
# Step 1: 임베딩 기반 K-means (k=10)
clusters = kmeans(embeddings, n_clusters=10)

# Step 2: 각 클러스터 내에서만 조합
# C(10, 3) * 10 clusters = 1,200
```

**수정 파일:**
- `/backend/services/supabase_service.py` - Pre-filtering 로직
- `/backend/routers/pipeline.py` - max_candidates 파라미터

---

### 구현 우선순위

**Phase 1 (핵심):**
1. ✅ DB 스키마 변경 + 마이그레이션 (2-3시간)
2. ✅ 하이브리드 알고리즘 구현 (2-3시간)
3. ✅ LLM 프롬프트 재작성 (1-2시간)

**Phase 2 (최적화):**
4. 복잡도 완화 (Pre-filtering) (1시간)
5. 프론트엔드 N-pair 표시 (1시간)

**예상 총 소요 시간:** 5-8시간 (Phase 1), +2시간 (Phase 2)

---

### Critical Files

**수정 필요:**
- `/backend/docs/supabase_setup.sql` - 스키마 재설계
- `/backend/schemas/zk.py` - ThoughtCluster 모델
- `/backend/services/supabase_service.py` - 조합 알고리즘
- `/backend/services/ai_service.py` - 프롬프트 재작성
- `/backend/routers/pipeline.py` - Step 3 엔드포인트

**신규 생성:**
- `/backend/migrations/001_pair_to_cluster.sql` - 마이그레이션 스크립트

---

## 권장 순서

### 시나리오 A: 빠른 가치 창출
1. **옵션 1 (Notion 대량 수집) - Phase 1** (4-6시간)
   - 즉시 사용 가능, 데이터 풍부화
   - 기존 아키텍처 유지
2. **옵션 2 (N-pair) - Phase 1** (5-8시간)
   - 창의성 증가
   - 아키텍처 변경 필요

**총 소요 시간:** 9-14시간 (약 2일)

### 시나리오 B: 단계적 접근
1. **옵션 1 Phase 1** (4-6시간) → 배포 & 피드백
2. **옵션 2 Phase 1** (5-8시간) → 배포 & 피드백
3. Phase 2 최적화들

**총 소요 시간:** 1주일 (여유 있는 일정)

---

## 검증 계획

### 옵션 1 검증:
```bash
# 1. 페이지네이션 테스트
POST /pipeline/import-from-notion?page_size=100
→ 100개 이상 수집 확인

# 2. 본문 수집 확인
SELECT content FROM raw_notes WHERE content IS NOT NULL;

# 3. Rate limiting 확인
→ 로그에서 429 에러 없음 확인
```

### 옵션 2 검증:
```bash
# 1. 3-pair 생성 테스트
POST /pipeline/select-pairs?cluster_size=3

# 2. DB 확인
SELECT * FROM thought_clusters;
SELECT * FROM cluster_thoughts;

# 3. Essay 생성 (3개 thought)
POST /pipeline/generate-essays

# 4. 프론트엔드 표시 확인
→ used_thoughts_json에 3개 항목 표시
```

---

## 복잡도 상세 분석: 2-pair → N-pair

### 수학적 기초: 조합 공식

**C(n, k) = n! / (k! × (n-k)!)**

- n = 전체 thought 개수
- k = 선택할 thought 개수 (pair size)

---

### 실제 데이터 규모별 복잡도

#### 시나리오 1: 소규모 (n=10, 현재 상태)

| Pair Size | 조합 수 | 증가율 | LLM 비용 | 소요 시간 |
|-----------|---------|--------|----------|-----------|
| 2-pair    | 45      | -      | $0.14    | 1분       |
| 3-pair    | 120     | 2.67x  | $0.36    | 2분       |
| 4-pair    | 210     | 1.75x  | $0.63    | 3분       |
| 5-pair    | 252     | 1.20x  | $0.76    | 4분       |

**결론:** n=10일 때는 문제 없음

---

#### 시나리오 2: 중규모 (n=50)

| Pair Size | 조합 수    | 증가율 | LLM 비용 | 소요 시간 |
|-----------|-----------|--------|----------|-----------|
| 2-pair    | 1,225     | -      | $3.68    | 5분       |
| 3-pair    | 19,600    | 16x    | $58.80   | 1시간     |
| 4-pair    | 230,300   | 11.8x  | $690.90  | 12시간    |
| 5-pair    | 2,118,760 | 9.2x   | $6,356   | 5일       |

**결론:** 3-pair까지 실용 가능, 4-pair부터 비현실적

---

#### 시나리오 3: 대규모 (n=100, 목표 규모)

| Pair Size | 조합 수      | 증가율 | LLM 비용  | 소요 시간 |
|-----------|-------------|--------|-----------|-----------|
| 2-pair    | 4,950       | -      | $14.85    | 10분      |
| 3-pair    | 161,700     | 32.7x  | $485.10   | 5시간     |
| 4-pair    | 3,921,225   | 24.3x  | $11,763   | 5일       |
| 5-pair    | 75,287,520  | 19.2x  | $225,863  | 3개월     |

**결론:**
- 2-pair: ✅ 실용적 (현재 구현)
- 3-pair: ⚠️ 한계선 (필터링 필수)
- 4-pair 이상: ❌ 불가능

**비용 계산 가정:**
- Claude 3.5 Sonnet: $3/M input, $15/M output
- 평가 1회 = 500 tokens input + 100 tokens output
- 평가 1회당 비용 = $0.003

---

### 하이브리드 전략 (Hybrid Strategy) 상세 설명

#### 개념

**순수 조합 방식 (Naive):**
```
모든 C(n, k) 조합을 생성 → LLM 평가 → 상위 N개 선택
문제: k=3, n=100일 때 161,700개 평가 필요
```

**하이브리드 방식 (Hybrid):**
```
Step 1: 기존 2-pair 알고리즘으로 좋은 페어 선택 (검증된 로직)
Step 2: 각 페어에 3번째 thought만 추가 (점진적 확장)
이점: 복잡도 O(n³) → O(n²)
```

---

#### 알고리즘 상세

**Phase 1: 2-pair 선택 (기존 로직 재사용)**
```python
# 1. pgvector로 유사도 범위 필터링 (0.05-0.35)
candidates = find_candidate_pairs(min_sim=0.05, max_sim=0.35)
# 결과: 약 500-1000 페어

# 2. LLM으로 창의성 평가
scored_pairs = score_pairs(candidates)
# 프롬프트: "두 아이디어의 창의적 연결 가능성"

# 3. 상위 50개 선택 (threshold >= 75점)
top_pairs = select_top(scored_pairs, top_n=50, min_score=75)
```

**Phase 2: 3번째 thought 추가 (새로운 로직)**
```python
triplets = []

for pair in top_pairs:  # 50번 반복
    # 페어와 "약한 연결"된 3번째 thought 찾기
    third_candidates = find_complementary_thought(
        pair.thought_a_id,
        pair.thought_b_id,
        min_sim=0.05,  # 페어와 낮은 유사도
        max_sim=0.35,
        limit=30       # 상위 30개만 (복잡도 제한)
    )

    # 50 pairs × 30 candidates = 1,500 triplets

    # LLM으로 3개 조합 평가
    for third in third_candidates:
        triplet = {
            "thought_ids": [pair.a_id, pair.b_id, third.id],
            "thoughts": [pair.a, pair.b, third],
            "avg_similarity": calculate_avg_sim([pair.a, pair.b, third])
        }

        score = score_triplet(triplet)
        # 프롬프트: "세 아이디어의 창의적 조합 가능성"

        if score >= 70:
            triplets.append(triplet)

# 결과: 약 100-200개 고품질 3-pair
```

**Phase 3: 최종 선택**
```python
# LLM 평가 점수 기준 상위 N개
final_clusters = select_top(triplets, top_n=10)
```

---

#### 복잡도 비교

**순수 조합 (Naive):**
```
C(100, 3) = 161,700
LLM 호출: 161,700회
비용: $485
시간: 5시간
```

**하이브리드 (Hybrid):**
```
Phase 1: C(100, 2) = 4,950 (기존)
Phase 2: 50 pairs × 30 candidates = 1,500

총 LLM 호출: 4,950 + 1,500 = 6,450
비용: $19.35
시간: 30분

복잡도 감소: 96% ↓ (161,700 → 6,450)
```

---

#### 하이브리드 전략의 장점

**1. 복잡도 제어**
- O(n³) → O(n² + kn) where k=50 (상수)
- n=200까지 확장 가능

**2. 품질 보장**
- Phase 1에서 이미 검증된 좋은 페어 기반
- "좋은 2개 + 보완적 1개" 구조로 창의성 극대화

**3. 기존 로직 재사용**
- 2-pair 알고리즘 그대로 사용 (검증됨)
- 최소한의 코드 변경

**4. 점진적 확장 가능**
- 3-pair → 4-pair 확장 시에도 동일 패턴 적용
- 4-pair: 50 pairs × 20 thirds × 10 fourths = 10,000 (vs 3.9M)

---

#### 왜 "하이브리드"인가?

**"Naive Combination" + "Greedy Extension"의 하이브리드**

1. **Naive 부분 (Phase 1):**
   - 모든 C(n,2) 조합 생성 (완전 탐색)
   - 품질 보장

2. **Greedy 부분 (Phase 2):**
   - 좋은 페어에만 3번째 추가 (탐욕적 선택)
   - 복잡도 절감

**결과:** 품질은 유지하면서 복잡도만 감소

---

### 복잡도 완화 전략 비교

| 전략 | 복잡도 감소 | 품질 | 구현 난이도 | 추천도 |
|------|-------------|------|-------------|--------|
| **하이브리드** | 96% ↓ | ⭐⭐⭐⭐⭐ | 중간 | ⭐⭐⭐⭐⭐ |
| Pre-filtering | 97% ↓ | ⭐⭐⭐⭐ | 쉬움 | ⭐⭐⭐⭐ |
| Random Sampling | 97% ↓ | ⭐⭐ | 쉬움 | ⭐⭐ |
| K-means Clustering | 99% ↓ | ⭐⭐⭐ | 어려움 | ⭐⭐⭐ |

**하이브리드 전략이 최선인 이유:**
- 품질과 복잡도 모두 우수
- 기존 코드 재사용으로 안정성 확보
- 점진적 확장 가능

---

### N-pair 확장 한계

**일반 공식 (Naive):**
```
k=2: C(n,2) = n²/2
k=3: C(n,3) = n³/6
k=4: C(n,4) = n⁴/24
k=5: C(n,5) = n⁵/120
```

**하이브리드 전략 (실제 복잡도):**
```
k=2: O(n²)           → 4,950 (n=100)
k=3: O(n²)           → 6,450 (n=100)
k=4: O(n² + n)       → 10,000 추정
k=5: O(n² + n²)      → 25,000 추정
```

**권장 사항:**
- ✅ **3-pair까지만 구현** (충분히 창의적)
- ⚠️ 4-pair는 신중히 검토 (필요성 의문)
- ❌ 5-pair 이상은 비추천 (LLM도 혼란)

---

### 실전 적용 시나리오

**목표: Notion 메모 100개 → Essay 생성**

```
Step 1: Notion Import (옵션 1)
→ 100개 페이지 수집

Step 2: Extract Thoughts
→ 100 pages × 평균 2 thoughts = 200 thoughts

Step 3: Select 3-pair Clusters (옵션 2 하이브리드)
→ Phase 1: C(200,2) = 19,900 (2-pair 평가)
→ Phase 2: 50 pairs × 30 = 1,500 (3-pair 평가)
→ 총 비용: $64
→ 소요 시간: 1시간

Step 4: Generate Essays
→ 상위 10개 cluster 선택
→ 10개 Essay 생성
```

**결과:**
- 10개 고품질 Essay (3개 thought 조합)
- 총 비용: $70 (Notion 200개 기준)
- 총 시간: 2시간

---

## 최종 추천

**추천 순서: 옵션 1 → 옵션 2 (하이브리드 전략)**

**이유:**
1. **옵션 1**은 즉시 가치 제공 (더 많은 메모 → 더 많은 Essay)
2. **옵션 2**는 하이브리드 전략으로 복잡도 문제 해결
3. 데이터가 충분해야 N-pair 효과 극대화
4. 옵션 1로 데이터 확보 후 옵션 2 실험이 안전

**하이브리드 전략 채택 이유:**
- 복잡도 96% 감소 (161K → 6.5K)
- 품질 보장 (좋은 2-pair 기반)
- 기존 로직 재사용 (안정성)
- 3-pair 구현으로 충분한 창의성

**단, 동시 진행 가능:**
- 두 옵션이 서로 독립적 (DB 충돌 없음)
- 병렬 작업 가능 (별도 브랜치)
