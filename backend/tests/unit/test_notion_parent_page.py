"""
Unit tests for NotionService.fetch_child_pages_from_parent() method.

Tests:
1. Happy path - single batch with child pages
2. Happy path - multiple batches (pagination)
3. Happy path - filters only child_page blocks (ignores other block types)
4. Edge case - empty parent page (no child pages)
5. Edge case - parent page with only non-child_page blocks
6. Edge case - has_more=True but no next_cursor
7. Error handling - API failure with retry
8. Error handling - 429 rate limit with exponential backoff
9. Error handling - pages.retrieve() fails for some child pages (partial success)
10. Rate limiting is applied correctly
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from notion_client.errors import APIResponseError
from services.notion_service import NotionService


class TestFetchChildPagesFromParent:
    """Tests for fetch_child_pages_from_parent() method"""

    @pytest.mark.asyncio
    async def test_single_batch_with_child_pages(self):
        """Single batch with child_page blocks returns all child pages"""
        # Setup
        service = NotionService()
        service.rate_limiter.acquire = AsyncMock()

        # Mock blocks.children.list() response
        mock_blocks_response = {
            "results": [
                {"type": "child_page", "id": "child-page-1"},
                {"type": "child_page", "id": "child-page-2"},
                {"type": "paragraph", "id": "paragraph-1"},  # Should be filtered out
            ],
            "has_more": False,
            "next_cursor": None
        }

        # Mock pages.retrieve() responses
        mock_page_1 = {
            "id": "child-page-1",
            "created_time": "2024-01-01T00:00:00Z",
            "last_edited_time": "2024-01-01T00:00:00Z",
            "properties": {"제목": "Child Page 1"}
        }
        mock_page_2 = {
            "id": "child-page-2",
            "created_time": "2024-01-02T00:00:00Z",
            "last_edited_time": "2024-01-02T00:00:00Z",
            "properties": {"제목": "Child Page 2"}
        }

        service.client.blocks.children.list = MagicMock(return_value=mock_blocks_response)
        service.client.pages.retrieve = MagicMock(side_effect=[mock_page_1, mock_page_2])

        # Execute
        result = await service.fetch_child_pages_from_parent(
            parent_page_id="parent-page-id"
        )

        # Assert
        assert len(result) == 2
        assert result[0]["id"] == "child-page-1"
        assert result[1]["id"] == "child-page-2"

        # Verify blocks.children.list called once
        service.client.blocks.children.list.assert_called_once()
        call_kwargs = service.client.blocks.children.list.call_args[1]
        assert call_kwargs["block_id"] == "parent-page-id"
        assert call_kwargs["page_size"] == 100

        # Verify pages.retrieve NOT called (optimization: get data from blocks.children.list)
        assert service.client.pages.retrieve.call_count == 0

        # Verify rate limiter called once (only for blocks.children.list)
        assert service.rate_limiter.acquire.call_count == 1

    @pytest.mark.asyncio
    async def test_multiple_batches_pagination(self):
        """Multiple batches with has_more=True triggers pagination loop"""
        # Setup
        service = NotionService()
        service.rate_limiter.acquire = AsyncMock()

        # Batch 1 - 2 child pages
        batch1 = {
            "results": [
                {"type": "child_page", "id": "child-1"},
                {"type": "child_page", "id": "child-2"},
            ],
            "has_more": True,
            "next_cursor": "cursor_batch2"
        }

        # Batch 2 - 1 child page
        batch2 = {
            "results": [
                {"type": "child_page", "id": "child-3"},
            ],
            "has_more": False,
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(side_effect=[batch1, batch2])

        # Mock pages.retrieve for all 3 child pages
        mock_pages = [
            {"id": f"child-{i}", "properties": {}} for i in range(1, 4)
        ]
        service.client.pages.retrieve = MagicMock(side_effect=mock_pages)

        # Execute
        result = await service.fetch_child_pages_from_parent(
            parent_page_id="parent-page-id"
        )

        # Assert
        assert len(result) == 3
        assert result[0]["id"] == "child-1"
        assert result[1]["id"] == "child-2"
        assert result[2]["id"] == "child-3"

        # Verify blocks.children.list called twice with correct cursors
        assert service.client.blocks.children.list.call_count == 2

        # First call - no cursor
        call1_kwargs = service.client.blocks.children.list.call_args_list[0][1]
        assert "start_cursor" not in call1_kwargs

        # Second call - with cursor
        call2_kwargs = service.client.blocks.children.list.call_args_list[1][1]
        assert call2_kwargs["start_cursor"] == "cursor_batch2"

        # Verify pages.retrieve NOT called (optimization)
        assert service.client.pages.retrieve.call_count == 0

    @pytest.mark.asyncio
    async def test_filters_only_child_page_blocks(self):
        """Only child_page blocks are processed, other block types are ignored"""
        # Setup
        service = NotionService()
        service.rate_limiter.acquire = AsyncMock()

        # Mixed block types
        mock_blocks_response = {
            "results": [
                {"type": "child_page", "id": "child-1"},
                {"type": "paragraph", "id": "para-1"},
                {"type": "heading_1", "id": "h1-1"},
                {"type": "child_page", "id": "child-2"},
                {"type": "bulleted_list_item", "id": "bullet-1"},
                {"type": "child_database", "id": "db-1"},  # Not a child_page
            ],
            "has_more": False,
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(return_value=mock_blocks_response)
        service.client.pages.retrieve = MagicMock(side_effect=[
            {"id": "child-1", "properties": {}},
            {"id": "child-2", "properties": {}},
        ])

        # Execute
        result = await service.fetch_child_pages_from_parent("parent-page-id")

        # Assert
        assert len(result) == 2  # Only 2 child_page blocks
        assert result[0]["id"] == "child-1"
        assert result[1]["id"] == "child-2"

        # Verify pages.retrieve NOT called (optimization)
        assert service.client.pages.retrieve.call_count == 0

    @pytest.mark.asyncio
    async def test_empty_parent_page(self):
        """Parent page with no blocks returns empty list gracefully"""
        # Setup
        service = NotionService()
        service.rate_limiter.acquire = AsyncMock()

        mock_response = {
            "results": [],
            "has_more": False,
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(return_value=mock_response)

        # Execute
        result = await service.fetch_child_pages_from_parent("parent-page-id")

        # Assert
        assert result == []
        assert len(result) == 0
        service.client.blocks.children.list.assert_called_once()

    @pytest.mark.asyncio
    async def test_parent_with_only_non_child_page_blocks(self):
        """Parent page with only non-child_page blocks returns empty list"""
        # Setup
        service = NotionService()
        service.rate_limiter.acquire = AsyncMock()

        mock_response = {
            "results": [
                {"type": "paragraph", "id": "para-1"},
                {"type": "heading_1", "id": "h1-1"},
                {"type": "bulleted_list_item", "id": "bullet-1"},
            ],
            "has_more": False,
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(return_value=mock_response)
        service.client.pages.retrieve = MagicMock()  # Initialize as mock

        # Execute
        result = await service.fetch_child_pages_from_parent("parent-page-id")

        # Assert
        assert result == []
        assert len(result) == 0

        # Verify pages.retrieve was never called
        service.client.pages.retrieve.assert_not_called()

    @pytest.mark.asyncio
    async def test_has_more_true_but_no_cursor_stops(self):
        """has_more=True but no next_cursor stops pagination (API inconsistency)"""
        # Setup
        service = NotionService()
        service.rate_limiter.acquire = AsyncMock()

        mock_response = {
            "results": [
                {"type": "child_page", "id": "child-1"},
            ],
            "has_more": True,  # Inconsistent: says more but no cursor
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(return_value=mock_response)
        service.client.pages.retrieve = MagicMock(return_value={"id": "child-1", "properties": {}})

        # Execute
        result = await service.fetch_child_pages_from_parent("parent-page-id")

        # Assert - should stop after first batch
        assert len(result) == 1
        service.client.blocks.children.list.assert_called_once()

    @pytest.mark.asyncio
    async def test_api_failure_with_retry(self):
        """API failure triggers retry logic"""
        # Setup
        service = NotionService()
        service.rate_limiter.acquire = AsyncMock()
        service.backoff.sleep = AsyncMock()

        # First call fails, second call succeeds
        mock_success_response = {
            "results": [{"type": "child_page", "id": "child-1"}],
            "has_more": False,
            "next_cursor": None
        }

        error = Exception("Notion API Error: Connection timeout")
        service.client.blocks.children.list = MagicMock(
            side_effect=[error, mock_success_response]
        )
        service.client.pages.retrieve = MagicMock(return_value={"id": "child-1", "properties": {}})

        # Execute
        result = await service.fetch_child_pages_from_parent("parent-page-id")

        # Assert
        assert len(result) == 1
        assert result[0]["id"] == "child-1"

        # Verify retry happened
        assert service.client.blocks.children.list.call_count == 2
        assert service.backoff.sleep.call_count == 1

    @pytest.mark.asyncio
    async def test_429_rate_limit_with_exponential_backoff(self):
        """429 rate limit error triggers exponential backoff retry"""
        # Setup
        service = NotionService()
        service.rate_limiter.acquire = AsyncMock()
        service.backoff.sleep = AsyncMock()

        # Mock 429 error
        rate_limit_error = APIResponseError(
            response=MagicMock(status_code=429),
            message="Rate limited",
            code=429
        )

        mock_success_response = {
            "results": [{"type": "child_page", "id": "child-1"}],
            "has_more": False,
            "next_cursor": None
        }

        # First call: 429, second call: success
        service.client.blocks.children.list = MagicMock(
            side_effect=[rate_limit_error, mock_success_response]
        )
        service.client.pages.retrieve = MagicMock(return_value={"id": "child-1", "properties": {}})

        # Execute
        result = await service.fetch_child_pages_from_parent("parent-page-id")

        # Assert
        assert len(result) == 1
        assert result[0]["id"] == "child-1"

        # Verify backoff.sleep was called for retry
        assert service.backoff.sleep.call_count == 1
        assert service.client.blocks.children.list.call_count == 2

    @pytest.mark.asyncio
    async def test_all_child_pages_retrieved_without_pages_retrieve(self):
        """All child pages are retrieved from blocks.children.list without calling pages.retrieve"""
        # Setup
        service = NotionService()
        service.rate_limiter.acquire = AsyncMock()

        mock_blocks_response = {
            "results": [
                {"type": "child_page", "id": "child-1", "created_time": "2024-01-01T00:00:00Z", "last_edited_time": "2024-01-01T00:00:00Z", "child_page": {"title": "Page 1"}},
                {"type": "child_page", "id": "child-2", "created_time": "2024-01-02T00:00:00Z", "last_edited_time": "2024-01-02T00:00:00Z", "child_page": {"title": "Page 2"}},
                {"type": "child_page", "id": "child-3", "created_time": "2024-01-03T00:00:00Z", "last_edited_time": "2024-01-03T00:00:00Z", "child_page": {"title": "Page 3"}},
            ],
            "has_more": False,
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(return_value=mock_blocks_response)
        service.client.pages.retrieve = MagicMock()

        # Execute
        result = await service.fetch_child_pages_from_parent("parent-page-id")

        # Assert - all 3 pages retrieved
        assert len(result) == 3
        assert result[0]["id"] == "child-1"
        assert result[1]["id"] == "child-2"
        assert result[2]["id"] == "child-3"

        # Verify pages.retrieve was NOT called (optimization)
        assert service.client.pages.retrieve.call_count == 0

    @pytest.mark.asyncio
    async def test_rate_limiter_called_correctly(self):
        """Rate limiter is called for each API operation"""
        # Setup
        service = NotionService()
        service.rate_limiter.acquire = AsyncMock()

        mock_blocks_response = {
            "results": [
                {"type": "child_page", "id": "child-1"},
                {"type": "child_page", "id": "child-2"},
            ],
            "has_more": False,
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(return_value=mock_blocks_response)
        service.client.pages.retrieve = MagicMock(side_effect=[
            {"id": "child-1", "properties": {}},
            {"id": "child-2", "properties": {}},
        ])

        # Execute
        result = await service.fetch_child_pages_from_parent("parent-page-id")

        # Assert
        assert len(result) == 2

        # Verify rate limiter called only once (for blocks.children.list, no pages.retrieve)
        assert service.rate_limiter.acquire.call_count == 1

    @pytest.mark.asyncio
    async def test_max_retries_exceeded_raises_exception(self):
        """Max retries exceeded raises exception"""
        # Setup
        service = NotionService()
        service.rate_limiter.acquire = AsyncMock()
        service.backoff.sleep = AsyncMock()

        # Mock persistent failure (more failures than max_retries)
        # The code raises the last exception, not a custom "Max retries" message
        error = Exception("Persistent API Error")
        service.client.blocks.children.list = MagicMock(
            side_effect=[error, error, error, error]  # 4 failures
        )

        # Execute & Assert
        with pytest.raises(Exception) as exc_info:
            await service.fetch_child_pages_from_parent("parent-page-id")

        # The original error is raised after retries
        assert "Persistent API Error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_custom_page_size(self):
        """Custom page_size parameter is respected"""
        # Setup
        service = NotionService()
        service.rate_limiter.acquire = AsyncMock()

        mock_response = {
            "results": [{"type": "child_page", "id": "child-1"}],
            "has_more": False,
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(return_value=mock_response)
        service.client.pages.retrieve = MagicMock(return_value={"id": "child-1", "properties": {}})

        # Execute
        result = await service.fetch_child_pages_from_parent(
            parent_page_id="parent-page-id",
            page_size=50
        )

        # Assert
        assert len(result) == 1
        call_kwargs = service.client.blocks.children.list.call_args[1]
        assert call_kwargs["page_size"] == 50

    @pytest.mark.asyncio
    async def test_missing_results_key_handled(self):
        """Missing 'results' key in API response is handled gracefully"""
        # Setup
        service = NotionService()
        service.rate_limiter.acquire = AsyncMock()

        # API response without 'results' key
        mock_response = {
            "has_more": False,
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(return_value=mock_response)

        # Execute
        result = await service.fetch_child_pages_from_parent("parent-page-id")

        # Assert - should handle gracefully
        assert result == []
        assert len(result) == 0
