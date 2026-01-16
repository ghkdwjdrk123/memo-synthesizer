"""
Unit tests for NotionService pagination loop (fetch_all_database_pages).

Tests:
1. Happy path - single batch (has_more=False)
2. Happy path - multiple batches (has_more=True, pagination loop)
3. Edge case - empty database
4. Edge case - exactly 100 pages (boundary test)
5. Edge case - has_more=True but no next_cursor (API inconsistency)
6. Error handling - API failure mid-pagination
7. start_cursor is correctly passed between batches
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from services.notion_service import NotionService


class TestFetchAllDatabasePages:
    """Tests for fetch_all_database_pages() method"""

    @pytest.mark.asyncio
    async def test_single_batch_no_pagination(self):
        """Single batch with has_more=False returns all pages"""
        # Setup
        service = NotionService()
        service.rate_limiter.acquire = AsyncMock()  # Mock rate limiter for faster tests

        mock_response = {
            "results": [
                {"id": "page1", "created_time": "2024-01-01T00:00:00Z", "last_edited_time": "2024-01-01T00:00:00Z"},
                {"id": "page2", "created_time": "2024-01-02T00:00:00Z", "last_edited_time": "2024-01-02T00:00:00Z"},
            ],
            "has_more": False,
            "next_cursor": None
        }

        service.client.databases.query = MagicMock(return_value=mock_response)

        # Execute
        result = await service.fetch_all_database_pages(page_size=100)

        # Assert
        assert len(result) == 2
        assert result[0]["id"] == "page1"
        assert result[1]["id"] == "page2"

        # Verify API called only once
        service.client.databases.query.assert_called_once()
        call_args = service.client.databases.query.call_args
        assert call_args[1]["page_size"] == 100
        assert "start_cursor" not in call_args[1]

    @pytest.mark.asyncio
    async def test_multiple_batches_pagination_loop(self):
        """Multiple batches with has_more=True triggers pagination loop"""
        # Setup
        service = NotionService()

        # Simulate 3 batches
        batch1 = {
            "results": [{"id": f"page{i}"} for i in range(1, 101)],  # 100 pages
            "has_more": True,
            "next_cursor": "cursor_batch2"
        }
        batch2 = {
            "results": [{"id": f"page{i}"} for i in range(101, 201)],  # 100 pages
            "has_more": True,
            "next_cursor": "cursor_batch3"
        }
        batch3 = {
            "results": [{"id": f"page{i}"} for i in range(201, 251)],  # 50 pages
            "has_more": False,
            "next_cursor": None
        }

        service.client.databases.query = MagicMock(
            side_effect=[batch1, batch2, batch3]
        )

        # Execute
        result = await service.fetch_all_database_pages(page_size=100)

        # Assert
        assert len(result) == 250  # Total pages across 3 batches
        assert result[0]["id"] == "page1"
        assert result[99]["id"] == "page100"
        assert result[100]["id"] == "page101"
        assert result[249]["id"] == "page250"

        # Verify API called 3 times with correct cursors
        assert service.client.databases.query.call_count == 3

        # First call - no cursor
        call1_kwargs = service.client.databases.query.call_args_list[0][1]
        assert "start_cursor" not in call1_kwargs

        # Second call - cursor_batch2
        call2_kwargs = service.client.databases.query.call_args_list[1][1]
        assert call2_kwargs["start_cursor"] == "cursor_batch2"

        # Third call - cursor_batch3
        call3_kwargs = service.client.databases.query.call_args_list[2][1]
        assert call3_kwargs["start_cursor"] == "cursor_batch3"

    @pytest.mark.asyncio
    async def test_empty_database(self):
        """Empty database returns empty list gracefully"""
        # Setup
        service = NotionService()

        mock_response = {
            "results": [],
            "has_more": False,
            "next_cursor": None
        }

        service.client.databases.query = MagicMock(return_value=mock_response)

        # Execute
        result = await service.fetch_all_database_pages()

        # Assert
        assert result == []
        assert len(result) == 0
        service.client.databases.query.assert_called_once()

    @pytest.mark.asyncio
    async def test_exactly_100_pages_boundary(self):
        """Exactly 100 pages (page_size boundary) handles correctly"""
        # Setup
        service = NotionService()

        # First batch - exactly 100 pages
        batch1 = {
            "results": [{"id": f"page{i}"} for i in range(1, 101)],
            "has_more": True,  # Notion might say there's more
            "next_cursor": "cursor_batch2"
        }
        # Second batch - empty (no more pages)
        batch2 = {
            "results": [],
            "has_more": False,
            "next_cursor": None
        }

        service.client.databases.query = MagicMock(
            side_effect=[batch1, batch2]
        )

        # Execute
        result = await service.fetch_all_database_pages(page_size=100)

        # Assert
        assert len(result) == 100
        assert service.client.databases.query.call_count == 2

    @pytest.mark.asyncio
    async def test_has_more_true_but_no_cursor_stops(self):
        """has_more=True but no next_cursor stops pagination (API inconsistency)"""
        # Setup
        service = NotionService()

        mock_response = {
            "results": [{"id": "page1"}],
            "has_more": True,  # Inconsistent: says more but no cursor
            "next_cursor": None
        }

        service.client.databases.query = MagicMock(return_value=mock_response)

        # Execute
        result = await service.fetch_all_database_pages()

        # Assert - should stop after first batch despite has_more=True
        assert len(result) == 1
        assert result[0]["id"] == "page1"
        service.client.databases.query.assert_called_once()

    @pytest.mark.asyncio
    async def test_api_failure_mid_pagination_raises(self):
        """API failure during pagination raises exception after retries"""
        # Setup
        service = NotionService()

        # Mock backoff.sleep to make test run instantly
        service.backoff.sleep = AsyncMock()

        batch1 = {
            "results": [{"id": "page1"}],
            "has_more": True,
            "next_cursor": "cursor_batch2"
        }

        # Provide enough exceptions for all retry attempts (1 initial + 3 retries = 4 total)
        error = Exception("Notion API Error: Rate limit exceeded")
        service.client.databases.query = MagicMock(
            side_effect=[
                batch1,
                error,
                error,
                error,
            ]
        )

        # Execute & Assert
        with pytest.raises(Exception) as exc_info:
            await service.fetch_all_database_pages()

        assert "Rate limit exceeded" in str(exc_info.value)
        # First batch succeeds (1), second batch fails and retries 2 times before final failure (3) = 4 total
        assert service.client.databases.query.call_count == 4
        # Verify backoff.sleep was called for retry attempts (sleep after 1st and 2nd failure, then raise on 3rd)
        assert service.backoff.sleep.call_count == 2

    @pytest.mark.asyncio
    async def test_custom_database_id(self):
        """Custom database_id parameter is used instead of default"""
        # Setup
        service = NotionService()
        custom_db_id = "custom-database-id-123"

        mock_response = {
            "results": [{"id": "page1"}],
            "has_more": False,
            "next_cursor": None
        }

        service.client.databases.query = MagicMock(return_value=mock_response)

        # Execute
        result = await service.fetch_all_database_pages(
            database_id=custom_db_id,
            page_size=50
        )

        # Assert
        assert len(result) == 1
        call_kwargs = service.client.databases.query.call_args[1]
        assert call_kwargs["database_id"] == custom_db_id
        assert call_kwargs["page_size"] == 50

    @pytest.mark.asyncio
    async def test_custom_page_size(self):
        """Custom page_size is respected"""
        # Setup
        service = NotionService()

        mock_response = {
            "results": [{"id": f"page{i}"} for i in range(1, 26)],  # 25 pages
            "has_more": False,
            "next_cursor": None
        }

        service.client.databases.query = MagicMock(return_value=mock_response)

        # Execute
        result = await service.fetch_all_database_pages(page_size=25)

        # Assert
        assert len(result) == 25
        call_kwargs = service.client.databases.query.call_args[1]
        assert call_kwargs["page_size"] == 25

    @pytest.mark.asyncio
    async def test_pagination_accumulates_correctly(self):
        """Pages from all batches are accumulated in order"""
        # Setup
        service = NotionService()

        batch1 = {
            "results": [
                {"id": "page1", "title": "First"},
                {"id": "page2", "title": "Second"}
            ],
            "has_more": True,
            "next_cursor": "cursor_batch2"
        }
        batch2 = {
            "results": [
                {"id": "page3", "title": "Third"},
                {"id": "page4", "title": "Fourth"}
            ],
            "has_more": False,
            "next_cursor": None
        }

        service.client.databases.query = MagicMock(
            side_effect=[batch1, batch2]
        )

        # Execute
        result = await service.fetch_all_database_pages()

        # Assert - order preserved
        assert len(result) == 4
        assert result[0]["id"] == "page1"
        assert result[1]["id"] == "page2"
        assert result[2]["id"] == "page3"
        assert result[3]["id"] == "page4"
        assert result[0]["title"] == "First"
        assert result[3]["title"] == "Fourth"

    @pytest.mark.asyncio
    async def test_missing_results_key_handled(self):
        """Missing 'results' key in API response is handled"""
        # Setup
        service = NotionService()

        # API response without 'results' key (should use empty list)
        mock_response = {
            "has_more": False,
            "next_cursor": None
        }

        service.client.databases.query = MagicMock(return_value=mock_response)

        # Execute
        result = await service.fetch_all_database_pages()

        # Assert - should handle gracefully
        assert result == []
        assert len(result) == 0
