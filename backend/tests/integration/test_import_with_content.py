"""
Integration tests for import-from-notion with block content (Phase 2).

Tests:
1. Import with empty page -> content=None
2. Import with content -> content populated
3. Import when block fetch fails -> page still saved
4. Import with short content (<10 chars) -> content=None
5. extract_thoughts uses content field
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from httpx import ASGITransport, AsyncClient
from datetime import datetime

from main import app


class TestImportWithContent:
    """Integration tests for import endpoint with block content collection"""

    @pytest.mark.asyncio
    async def test_import_empty_page_content_none(self):
        """Empty page with no blocks -> content=None"""
        # Mock pages
        mock_pages = [
            {
                "id": "test-page-1",
                "url": "https://notion.so/test-page-1",
                "created_time": "2024-01-01T00:00:00Z",
                "last_edited_time": "2024-01-01T00:00:00Z",
                "properties": {
                    "제목": "Empty Page"
                }
            }
        ]

        with patch('services.notion_service.NotionService.fetch_all_database_pages', new_callable=AsyncMock) as mock_fetch_all:
            with patch('services.notion_service.NotionService.fetch_page_blocks', new_callable=AsyncMock) as mock_fetch_blocks:
                with patch('services.supabase_service.SupabaseService.upsert_raw_note', new_callable=AsyncMock) as mock_upsert:
                    mock_fetch_all.return_value = mock_pages
                    mock_fetch_blocks.return_value = ""  # Empty blocks
                    mock_upsert.return_value = {"id": "uuid-1"}

                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        response = await client.post("/pipeline/import-from-notion?fetch_all=true")

                    # Assertions
                    assert response.status_code == 200
                    data = response.json()
                    assert data["success"] is True
                    assert data["imported_count"] == 1

                    # Verify content is None (short content < 10 chars)
                    upsert_call_args = mock_upsert.call_args[0][0]
                    assert upsert_call_args.content is None

    @pytest.mark.asyncio
    async def test_import_with_content_populated(self):
        """Page with blocks -> content field populated"""
        # Mock pages
        mock_pages = [
            {
                "id": "test-page-2",
                "url": "https://notion.so/test-page-2",
                "created_time": "2024-01-01T00:00:00Z",
                "last_edited_time": "2024-01-01T00:00:00Z",
                "properties": {
                    "제목": "Page with Content"
                }
            }
        ]

        # Rich content
        page_content = """# Main Title

This is a paragraph with meaningful content.

- Bullet point 1
- Bullet point 2

> This is a quote"""

        with patch('services.notion_service.NotionService.fetch_all_database_pages', new_callable=AsyncMock) as mock_fetch_all:
            with patch('services.notion_service.NotionService.fetch_page_blocks', new_callable=AsyncMock) as mock_fetch_blocks:
                with patch('services.supabase_service.SupabaseService.upsert_raw_note', new_callable=AsyncMock) as mock_upsert:
                    mock_fetch_all.return_value = mock_pages
                    mock_fetch_blocks.return_value = page_content
                    mock_upsert.return_value = {"id": "uuid-2"}

                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        response = await client.post("/pipeline/import-from-notion?fetch_all=true")

                    # Assertions
                    assert response.status_code == 200
                    data = response.json()
                    assert data["success"] is True
                    assert data["imported_count"] == 1

                    # Verify content is populated
                    upsert_call_args = mock_upsert.call_args[0][0]
                    assert upsert_call_args.content is not None
                    assert len(upsert_call_args.content) > 10
                    assert "Main Title" in upsert_call_args.content
                    assert "meaningful content" in upsert_call_args.content

    @pytest.mark.asyncio
    async def test_import_block_fetch_failure_page_still_saved(self):
        """Block fetch fails -> page is still saved with content=None"""
        # Mock pages
        mock_pages = [
            {
                "id": "test-page-3",
                "url": "https://notion.so/test-page-3",
                "created_time": "2024-01-01T00:00:00Z",
                "last_edited_time": "2024-01-01T00:00:00Z",
                "properties": {
                    "제목": "Page with API Error"
                }
            }
        ]

        with patch('services.notion_service.NotionService.fetch_all_database_pages', new_callable=AsyncMock) as mock_fetch_all:
            with patch('services.notion_service.NotionService.fetch_page_blocks', new_callable=AsyncMock) as mock_fetch_blocks:
                with patch('services.supabase_service.SupabaseService.upsert_raw_note', new_callable=AsyncMock) as mock_upsert:
                    mock_fetch_all.return_value = mock_pages
                    mock_fetch_blocks.side_effect = Exception("Notion API Error: blocks.children.list failed")
                    mock_upsert.return_value = {"id": "uuid-3"}

                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        response = await client.post("/pipeline/import-from-notion?fetch_all=true")

                    # Assertions - should succeed despite block fetch failure
                    assert response.status_code == 200
                    data = response.json()
                    assert data["success"] is True
                    assert data["imported_count"] == 1

                    # Verify page was saved with content=None
                    upsert_call_args = mock_upsert.call_args[0][0]
                    assert upsert_call_args.notion_page_id == "test-page-3"
                    assert upsert_call_args.content is None

    @pytest.mark.asyncio
    async def test_import_short_content_treated_as_none(self):
        """Content with < 10 characters is treated as None"""
        # Mock pages
        mock_pages = [
            {
                "id": "test-page-4",
                "url": "https://notion.so/test-page-4",
                "created_time": "2024-01-01T00:00:00Z",
                "last_edited_time": "2024-01-01T00:00:00Z",
                "properties": {
                    "제목": "Page with Short Content"
                }
            }
        ]

        with patch('services.notion_service.NotionService.fetch_all_database_pages', new_callable=AsyncMock) as mock_fetch_all:
            with patch('services.notion_service.NotionService.fetch_page_blocks', new_callable=AsyncMock) as mock_fetch_blocks:
                with patch('services.supabase_service.SupabaseService.upsert_raw_note', new_callable=AsyncMock) as mock_upsert:
                    mock_fetch_all.return_value = mock_pages
                    mock_fetch_blocks.return_value = "Hi"  # < 10 chars
                    mock_upsert.return_value = {"id": "uuid-4"}

                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        response = await client.post("/pipeline/import-from-notion?fetch_all=true")

                    # Assertions
                    assert response.status_code == 200
                    data = response.json()
                    assert data["success"] is True

                    # Verify content is None (too short)
                    upsert_call_args = mock_upsert.call_args[0][0]
                    assert upsert_call_args.content is None

    @pytest.mark.asyncio
    async def test_import_whitespace_only_content_treated_as_none(self):
        """Content with only whitespace is treated as None"""
        # Mock pages
        mock_pages = [
            {
                "id": "test-page-5",
                "url": "https://notion.so/test-page-5",
                "created_time": "2024-01-01T00:00:00Z",
                "last_edited_time": "2024-01-01T00:00:00Z",
                "properties": {
                    "제목": "Page with Whitespace Only"
                }
            }
        ]

        with patch('services.notion_service.NotionService.fetch_all_database_pages', new_callable=AsyncMock) as mock_fetch_all:
            with patch('services.notion_service.NotionService.fetch_page_blocks', new_callable=AsyncMock) as mock_fetch_blocks:
                with patch('services.supabase_service.SupabaseService.upsert_raw_note', new_callable=AsyncMock) as mock_upsert:
                    mock_fetch_all.return_value = mock_pages
                    mock_fetch_blocks.return_value = "   \n\n   "  # Whitespace only
                    mock_upsert.return_value = {"id": "uuid-5"}

                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        response = await client.post("/pipeline/import-from-notion?fetch_all=true")

                    # Assertions
                    assert response.status_code == 200
                    data = response.json()
                    assert data["success"] is True

                    # Verify content is None (only whitespace)
                    upsert_call_args = mock_upsert.call_args[0][0]
                    assert upsert_call_args.content is None


class TestExtractThoughtsUsesContent:
    """Tests that extract_thoughts endpoint uses content field"""

    @pytest.mark.asyncio
    async def test_extract_thoughts_uses_content_field(self):
        """extract_thoughts uses content field for thought extraction"""
        with patch('services.supabase_service.SupabaseService.get_raw_note_ids', new_callable=AsyncMock) as mock_get_ids:
            with patch('services.supabase_service.SupabaseService.get_raw_notes_by_ids', new_callable=AsyncMock) as mock_get_notes:
                with patch('services.ai_service.AIService.extract_thoughts', new_callable=AsyncMock) as mock_extract:
                    with patch('services.ai_service.AIService.create_embedding', new_callable=AsyncMock) as mock_embed:
                        with patch('services.supabase_service.SupabaseService.insert_thought_units_batch', new_callable=AsyncMock) as mock_insert:

                            mock_get_ids.return_value = ["uuid-1"]
                            mock_get_notes.return_value = [
                                {
                                    "id": "uuid-1",
                                    "title": "Test Note",
                                    "content": "# Main Heading\n\nThis is the body content from blocks.\n\n- Point 1\n- Point 2"
                                }
                            ]

                            from schemas.normalized import ThoughtExtractionResult, ThoughtUnit
                            mock_extract.return_value = ThoughtExtractionResult(
                                thoughts=[ThoughtUnit(claim="Test claim from content", context=None)]
                            )
                            mock_embed.return_value = {"success": True, "embedding": [0.1] * 1536}
                            mock_insert.return_value = [{"id": 1}]

                            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                                response = await client.post("/pipeline/extract-thoughts")

                            # Assertions
                            assert response.status_code == 200
                            data = response.json()
                            assert data["success"] is True
                            assert data["total_thoughts"] >= 1

                            # Verify extract_thoughts was called with content
                            extract_call = mock_extract.call_args
                            assert "body content from blocks" in extract_call[1]["content"]

    @pytest.mark.asyncio
    async def test_extract_thoughts_skips_note_without_title_or_content(self):
        """extract_thoughts skips notes with no title and no content"""
        with patch('services.supabase_service.SupabaseService.get_raw_note_ids', new_callable=AsyncMock) as mock_get_ids:
            with patch('services.supabase_service.SupabaseService.get_raw_notes_by_ids', new_callable=AsyncMock) as mock_get_notes:
                with patch('services.ai_service.AIService.extract_thoughts', new_callable=AsyncMock) as mock_extract:

                    mock_get_ids.return_value = ["uuid-1"]
                    mock_get_notes.return_value = [
                        {
                            "id": "uuid-1",
                            "title": "",
                            "content": None  # No content
                        }
                    ]

                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        response = await client.post("/pipeline/extract-thoughts")

                    # Assertions
                    assert response.status_code == 200
                    data = response.json()
                    assert data["success"] is True
                    assert data["total_thoughts"] == 0

                    # Verify extract_thoughts was NOT called (note was skipped)
                    mock_extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_extract_thoughts_uses_title_when_no_content(self):
        """extract_thoughts uses title when content is None"""
        with patch('services.supabase_service.SupabaseService.get_raw_note_ids', new_callable=AsyncMock) as mock_get_ids:
            with patch('services.supabase_service.SupabaseService.get_raw_notes_by_ids', new_callable=AsyncMock) as mock_get_notes:
                with patch('services.ai_service.AIService.extract_thoughts', new_callable=AsyncMock) as mock_extract:
                    with patch('services.ai_service.AIService.create_embedding', new_callable=AsyncMock) as mock_embed:
                        with patch('services.supabase_service.SupabaseService.insert_thought_units_batch', new_callable=AsyncMock) as mock_insert:

                            mock_get_ids.return_value = ["uuid-1"]
                            mock_get_notes.return_value = [
                                {
                                    "id": "uuid-1",
                                    "title": "Important Title Only",
                                    "content": None
                                }
                            ]

                            from schemas.normalized import ThoughtExtractionResult, ThoughtUnit
                            mock_extract.return_value = ThoughtExtractionResult(
                                thoughts=[ThoughtUnit(claim="Claim from title", context=None)]
                            )
                            mock_embed.return_value = {"success": True, "embedding": [0.1] * 1536}
                            mock_insert.return_value = [{"id": 1}]

                            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                                response = await client.post("/pipeline/extract-thoughts")

                            # Assertions
                            assert response.status_code == 200
                            data = response.json()
                            assert data["success"] is True
                            assert data["total_thoughts"] >= 1

                            # Verify extract_thoughts was called with title
                            extract_call = mock_extract.call_args
                            assert "Important Title Only" in extract_call[1]["title"]

    @pytest.mark.asyncio
    async def test_extract_thoughts_prefers_content_over_empty_title(self):
        """extract_thoughts prefers content when title is empty"""
        with patch('services.supabase_service.SupabaseService.get_raw_note_ids', new_callable=AsyncMock) as mock_get_ids:
            with patch('services.supabase_service.SupabaseService.get_raw_notes_by_ids', new_callable=AsyncMock) as mock_get_notes:
                with patch('services.ai_service.AIService.extract_thoughts', new_callable=AsyncMock) as mock_extract:
                    with patch('services.ai_service.AIService.create_embedding', new_callable=AsyncMock) as mock_embed:
                        with patch('services.supabase_service.SupabaseService.insert_thought_units_batch', new_callable=AsyncMock) as mock_insert:

                            mock_get_ids.return_value = ["uuid-1"]
                            mock_get_notes.return_value = [
                                {
                                    "id": "uuid-1",
                                    "title": "",  # Empty string instead of None
                                    "content": "Rich content from blocks without a title"
                                }
                            ]

                            from schemas.normalized import ThoughtExtractionResult, ThoughtUnit
                            mock_extract.return_value = ThoughtExtractionResult(
                                thoughts=[ThoughtUnit(claim="Claim from content", context=None)]
                            )
                            mock_embed.return_value = {"success": True, "embedding": [0.1] * 1536}
                            mock_insert.return_value = [{"id": 1}]

                            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                                response = await client.post("/pipeline/extract-thoughts")

                            # Assertions
                            assert response.status_code == 200
                            data = response.json()
                            assert data["success"] is True
                            assert data["total_thoughts"] >= 1

                            # Verify extract_thoughts was called with content
                            extract_call = mock_extract.call_args
                            assert "Rich content from blocks" in extract_call[1]["content"]
