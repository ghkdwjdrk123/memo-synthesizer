"""
Integration tests for Phase 1: Pagination loop in import-from-notion endpoint.

Focus: Test that the endpoint correctly uses fetch_all parameter and pagination logic.
Strategy: Patch at the service layer to avoid deep mock chains.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import ASGITransport, AsyncClient
from main import app


class TestImportPagination:
    """Integration tests for pagination in import-from-notion endpoint"""

    @pytest.mark.asyncio
    async def test_fetch_all_true_calls_fetch_all_database_pages(self):
        """fetch_all=True uses fetch_all_database_pages() method"""
        # Setup
        mock_pages = [
            {
                "id": f"page-{i}",
                "url": f"https://notion.so/page-{i}",
                "created_time": "2024-01-01T00:00:00Z",
                "last_edited_time": "2024-01-01T00:00:00Z",
                "properties": {"제목": f"Page {i}"}
            }
            for i in range(1, 251)  # 250 pages
        ]

        with patch('services.notion_service.NotionService.fetch_all_database_pages', new_callable=AsyncMock) as mock_fetch_all:
            with patch('services.supabase_service.SupabaseService.upsert_raw_note', new_callable=AsyncMock) as mock_upsert:
                mock_fetch_all.return_value = mock_pages
                mock_upsert.return_value = {"id": "uuid-123"}

                # Execute
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    response = await client.post(
                        "/pipeline/import-from-notion",
                        params={"fetch_all": True}
                    )

                # Assert
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                assert data["imported_count"] == 250

                # Verify fetch_all_database_pages was called (not query_database)
                mock_fetch_all.assert_called_once_with(page_size=100)

    @pytest.mark.asyncio
    async def test_fetch_all_false_calls_query_database(self):
        """fetch_all=False uses query_database() method (backward compatibility)"""
        # Setup
        mock_query_result = {
            "success": True,
            "total_count": 50,
            "has_more": True,
            "pages": [
                {
                    "id": f"page-{i}",
                    "url": f"https://notion.so/page-{i}",
                    "created_time": "2024-01-01T00:00:00Z",
                    "last_edited_time": "2024-01-01T00:00:00Z",
                    "properties": {"제목": f"Page {i}"}
                }
                for i in range(1, 51)
            ]
        }

        with patch('services.notion_service.NotionService.query_database', new_callable=AsyncMock) as mock_query:
            with patch('services.supabase_service.SupabaseService.upsert_raw_note', new_callable=AsyncMock) as mock_upsert:
                mock_query.return_value = mock_query_result
                mock_upsert.return_value = {"id": "uuid-123"}

                # Execute
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    response = await client.post(
                        "/pipeline/import-from-notion",
                        params={"fetch_all": False}
                    )

                # Assert
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                assert data["imported_count"] == 50  # Only from query_database

                # Verify query_database was called (not fetch_all_database_pages)
                mock_query.assert_called_once_with(page_size=100)

    @pytest.mark.asyncio
    async def test_default_fetch_all_is_true(self):
        """Default fetch_all parameter is True"""
        # Setup
        mock_pages = [
            {
                "id": "page-1",
                "url": "https://notion.so/page-1",
                "created_time": "2024-01-01T00:00:00Z",
                "last_edited_time": "2024-01-01T00:00:00Z",
                "properties": {"제목": "Page 1"}
            }
        ]

        with patch('services.notion_service.NotionService.fetch_all_database_pages', new_callable=AsyncMock) as mock_fetch_all:
            with patch('services.supabase_service.SupabaseService.upsert_raw_note', new_callable=AsyncMock) as mock_upsert:
                mock_fetch_all.return_value = mock_pages
                mock_upsert.return_value = {"id": "uuid-123"}

                # Execute - no params (default)
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    response = await client.post("/pipeline/import-from-notion")

                # Assert
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                assert data["imported_count"] == 1

                # Verify fetch_all_database_pages was called (default is True)
                mock_fetch_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_deduplication_removes_duplicates(self):
        """Duplicate pages in same session are filtered"""
        # Setup - API returns duplicates
        mock_pages = [
            {
                "id": "page-1",
                "url": "https://notion.so/page-1",
                "created_time": "2024-01-01T00:00:00Z",
                "last_edited_time": "2024-01-01T00:00:00Z",
                "properties": {"제목": "Page 1"}
            },
            {
                "id": "page-2",
                "url": "https://notion.so/page-2",
                "created_time": "2024-01-02T00:00:00Z",
                "last_edited_time": "2024-01-02T00:00:00Z",
                "properties": {"제목": "Page 2"}
            },
            {
                "id": "page-1",  # Duplicate
                "url": "https://notion.so/page-1",
                "created_time": "2024-01-01T00:00:00Z",
                "last_edited_time": "2024-01-01T00:00:00Z",
                "properties": {"제목": "Page 1"}
            },
        ]

        with patch('services.notion_service.NotionService.fetch_all_database_pages', new_callable=AsyncMock) as mock_fetch_all:
            with patch('services.supabase_service.SupabaseService.upsert_raw_note', new_callable=AsyncMock) as mock_upsert:
                mock_fetch_all.return_value = mock_pages
                mock_upsert.return_value = {"id": "uuid-123"}

                # Execute
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    response = await client.post(
                        "/pipeline/import-from-notion",
                        params={"fetch_all": True}
                    )

                # Assert
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                assert data["imported_count"] == 2  # Only 2 unique pages

                # Verify upsert called only twice (not 3 times)
                assert mock_upsert.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_pages_returns_success(self):
        """Empty pages list returns success with 0 imports"""
        # Setup
        with patch('services.notion_service.NotionService.fetch_all_database_pages', new_callable=AsyncMock) as mock_fetch_all:
            mock_fetch_all.return_value = []

            # Execute
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/pipeline/import-from-notion",
                    params={"fetch_all": True}
                )

            # Assert
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["imported_count"] == 0
            assert data["skipped_count"] == 0

    @pytest.mark.asyncio
    async def test_notion_api_failure_returns_500(self):
        """Notion API failure returns 500 error"""
        # Setup
        with patch('services.notion_service.NotionService.fetch_all_database_pages', new_callable=AsyncMock) as mock_fetch_all:
            mock_fetch_all.side_effect = Exception("Notion API Error: Rate limit exceeded")

            # Execute
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/pipeline/import-from-notion",
                    params={"fetch_all": True}
                )

            # Assert
            assert response.status_code == 500
            data = response.json()
            assert "Rate limit exceeded" in data["detail"]

    @pytest.mark.asyncio
    async def test_partial_failure_counts_skipped(self):
        """Some pages fail to import, others succeed"""
        # Setup
        pages = [
            {
                "id": "page-1",
                "url": "https://notion.so/page-1",
                "created_time": "2024-01-01T00:00:00Z",
                "last_edited_time": "2024-01-01T00:00:00Z",
                "properties": {"제목": "Good Page"}
            },
            {
                "id": "page-2",
                "url": "https://notion.so/page-2",
                # Missing created_time - will cause error
                "last_edited_time": "2024-01-02T00:00:00Z",
                "properties": {"제목": "Bad Page"}
            },
            {
                "id": "page-3",
                "url": "https://notion.so/page-3",
                "created_time": "2024-01-03T00:00:00Z",
                "last_edited_time": "2024-01-03T00:00:00Z",
                "properties": {"제목": "Good Page"}
            },
        ]

        with patch('services.notion_service.NotionService.fetch_all_database_pages', new_callable=AsyncMock) as mock_fetch_all:
            with patch('services.supabase_service.SupabaseService.upsert_raw_note', new_callable=AsyncMock) as mock_upsert:
                mock_fetch_all.return_value = pages
                mock_upsert.return_value = {"id": "uuid-123"}

                # Execute
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    response = await client.post(
                        "/pipeline/import-from-notion",
                        params={"fetch_all": True}
                    )

                # Assert - partial success
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                assert data["imported_count"] == 2  # page-1 and page-3
                assert data["skipped_count"] == 1  # page-2
                assert len(data["errors"]) == 1
                assert "page-2" in data["errors"][0]

    @pytest.mark.asyncio
    async def test_query_database_failure_returns_500(self):
        """query_database failure (fetch_all=False) returns 500"""
        # Setup
        with patch('services.notion_service.NotionService.query_database', new_callable=AsyncMock) as mock_query:
            mock_query.return_value = {
                "success": False,
                "error": "Database not found",
                "error_type": "ObjectNotFound"
            }

            # Execute
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/pipeline/import-from-notion",
                    params={"fetch_all": False}
                )

            # Assert
            assert response.status_code == 500
            data = response.json()
            assert "Notion query failed" in data["detail"]
            assert "Database not found" in data["detail"]
