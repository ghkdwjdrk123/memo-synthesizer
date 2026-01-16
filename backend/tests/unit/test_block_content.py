"""
Unit tests for NotionService block content extraction (Phase 2).

Tests:
1. _extract_rich_text - simple text array extraction
2. _extract_rich_text - empty array handling
3. _extract_rich_text - multiple text pieces concatenation
4. fetch_page_blocks - empty page (0 blocks)
5. fetch_page_blocks - paragraph blocks
6. fetch_page_blocks - heading_1/2/3 blocks with markdown conversion
7. fetch_page_blocks - bulleted_list_item and numbered_list_item
8. fetch_page_blocks - quote blocks
9. fetch_page_blocks - callout blocks (emoji + text)
10. fetch_page_blocks - code blocks with language
11. fetch_page_blocks - toggle blocks
12. fetch_page_blocks - mixed block types
13. fetch_page_blocks - pagination (100+ blocks)
14. fetch_page_blocks - API error returns partial content
"""

import pytest
from unittest.mock import MagicMock, patch
from services.notion_service import NotionService


class TestExtractRichText:
    """Tests for _extract_rich_text() helper method"""

    def test_simple_text_extraction(self):
        """Single plain_text item is extracted correctly"""
        service = NotionService()

        rich_text = [{"plain_text": "Hello World"}]

        result = service._extract_rich_text(rich_text)

        assert result == "Hello World"

    def test_empty_array_returns_empty_string(self):
        """Empty array returns empty string"""
        service = NotionService()

        result = service._extract_rich_text([])

        assert result == ""

    def test_multiple_text_pieces_concatenated(self):
        """Multiple text pieces are concatenated without spaces"""
        service = NotionService()

        rich_text = [
            {"plain_text": "Hello "},
            {"plain_text": "beautiful "},
            {"plain_text": "world"}
        ]

        result = service._extract_rich_text(rich_text)

        assert result == "Hello beautiful world"

    def test_missing_plain_text_key_handled(self):
        """Missing plain_text key is handled gracefully"""
        service = NotionService()

        rich_text = [
            {"plain_text": "Hello"},
            {},  # Missing plain_text
            {"plain_text": " World"}
        ]

        result = service._extract_rich_text(rich_text)

        assert result == "Hello World"

    def test_none_input_returns_empty_string(self):
        """None input returns empty string"""
        service = NotionService()

        result = service._extract_rich_text(None)

        assert result == ""


class TestFetchPageBlocks:
    """Tests for fetch_page_blocks() method"""

    @pytest.mark.asyncio
    async def test_empty_page_returns_empty_string(self):
        """Empty page with no blocks returns empty string"""
        service = NotionService()

        mock_response = {
            "results": [],
            "has_more": False,
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(return_value=mock_response)

        result = await service.fetch_page_blocks("test-page-id")

        assert result == ""
        service.client.blocks.children.list.assert_called_once_with(
            block_id="test-page-id",
            page_size=100
        )

    @pytest.mark.asyncio
    async def test_paragraph_blocks_extracted(self):
        """Paragraph blocks are extracted as plain text"""
        service = NotionService()

        mock_response = {
            "results": [
                {
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"plain_text": "This is a paragraph."}]
                    }
                },
                {
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"plain_text": "Another paragraph here."}]
                    }
                }
            ],
            "has_more": False,
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(return_value=mock_response)

        result = await service.fetch_page_blocks("test-page-id")

        assert "This is a paragraph." in result
        assert "Another paragraph here." in result
        assert result == "This is a paragraph.\n\nAnother paragraph here."

    @pytest.mark.asyncio
    async def test_heading_blocks_markdown_format(self):
        """Heading blocks are converted to markdown format"""
        service = NotionService()

        mock_response = {
            "results": [
                {
                    "type": "heading_1",
                    "heading_1": {
                        "rich_text": [{"plain_text": "Main Title"}]
                    }
                },
                {
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [{"plain_text": "Subtitle"}]
                    }
                },
                {
                    "type": "heading_3",
                    "heading_3": {
                        "rich_text": [{"plain_text": "Section"}]
                    }
                }
            ],
            "has_more": False,
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(return_value=mock_response)

        result = await service.fetch_page_blocks("test-page-id")

        assert "# Main Title" in result
        assert "## Subtitle" in result
        assert "### Section" in result

    @pytest.mark.asyncio
    async def test_list_blocks_formatted(self):
        """List blocks are formatted with bullets/numbers"""
        service = NotionService()

        mock_response = {
            "results": [
                {
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [{"plain_text": "First bullet"}]
                    }
                },
                {
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [{"plain_text": "Second bullet"}]
                    }
                },
                {
                    "type": "numbered_list_item",
                    "numbered_list_item": {
                        "rich_text": [{"plain_text": "First number"}]
                    }
                },
                {
                    "type": "numbered_list_item",
                    "numbered_list_item": {
                        "rich_text": [{"plain_text": "Second number"}]
                    }
                }
            ],
            "has_more": False,
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(return_value=mock_response)

        result = await service.fetch_page_blocks("test-page-id")

        assert "- First bullet" in result
        assert "- Second bullet" in result
        assert "1. First number" in result
        assert "1. Second number" in result

    @pytest.mark.asyncio
    async def test_quote_blocks_formatted(self):
        """Quote blocks are formatted with > prefix"""
        service = NotionService()

        mock_response = {
            "results": [
                {
                    "type": "quote",
                    "quote": {
                        "rich_text": [{"plain_text": "This is a quote"}]
                    }
                }
            ],
            "has_more": False,
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(return_value=mock_response)

        result = await service.fetch_page_blocks("test-page-id")

        assert "> This is a quote" in result

    @pytest.mark.asyncio
    async def test_callout_blocks_with_emoji(self):
        """Callout blocks include emoji and text"""
        service = NotionService()

        mock_response = {
            "results": [
                {
                    "type": "callout",
                    "callout": {
                        "rich_text": [{"plain_text": "Important note"}],
                        "icon": {"emoji": "⚠️"}
                    }
                },
                {
                    "type": "callout",
                    "callout": {
                        "rich_text": [{"plain_text": "Another callout"}],
                        "icon": {}  # No emoji
                    }
                }
            ],
            "has_more": False,
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(return_value=mock_response)

        result = await service.fetch_page_blocks("test-page-id")

        assert "⚠️ Important note" in result
        assert "💡 Another callout" in result  # Default emoji

    @pytest.mark.asyncio
    async def test_code_blocks_with_language(self):
        """Code blocks include language identifier"""
        service = NotionService()

        mock_response = {
            "results": [
                {
                    "type": "code",
                    "code": {
                        "rich_text": [{"plain_text": "print('hello')"}],
                        "language": "python"
                    }
                },
                {
                    "type": "code",
                    "code": {
                        "rich_text": [{"plain_text": "const x = 1;"}],
                        "language": "javascript"
                    }
                }
            ],
            "has_more": False,
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(return_value=mock_response)

        result = await service.fetch_page_blocks("test-page-id")

        assert "```python\nprint('hello')\n```" in result
        assert "```javascript\nconst x = 1;\n```" in result

    @pytest.mark.asyncio
    async def test_toggle_blocks_extracted(self):
        """Toggle blocks are extracted with arrow prefix"""
        service = NotionService()

        mock_response = {
            "results": [
                {
                    "type": "toggle",
                    "toggle": {
                        "rich_text": [{"plain_text": "Click to expand"}]
                    }
                }
            ],
            "has_more": False,
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(return_value=mock_response)

        result = await service.fetch_page_blocks("test-page-id")

        assert "▶ Click to expand" in result

    @pytest.mark.asyncio
    async def test_mixed_block_types(self):
        """Mixed block types are all extracted in order"""
        service = NotionService()

        mock_response = {
            "results": [
                {
                    "type": "heading_1",
                    "heading_1": {
                        "rich_text": [{"plain_text": "Title"}]
                    }
                },
                {
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"plain_text": "Some text"}]
                    }
                },
                {
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [{"plain_text": "Bullet"}]
                    }
                },
                {
                    "type": "quote",
                    "quote": {
                        "rich_text": [{"plain_text": "Quote"}]
                    }
                }
            ],
            "has_more": False,
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(return_value=mock_response)

        result = await service.fetch_page_blocks("test-page-id")

        # Check order is preserved
        lines = result.split("\n\n")
        assert lines[0] == "# Title"
        assert lines[1] == "Some text"
        assert lines[2] == "- Bullet"
        assert lines[3] == "> Quote"

    @pytest.mark.asyncio
    async def test_pagination_handles_100plus_blocks(self):
        """Pagination correctly handles 100+ blocks"""
        service = NotionService()

        # First batch - 100 blocks
        batch1 = {
            "results": [
                {
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"plain_text": f"Block {i}"}]
                    }
                }
                for i in range(1, 101)
            ],
            "has_more": True,
            "next_cursor": "cursor_batch2"
        }

        # Second batch - 50 blocks
        batch2 = {
            "results": [
                {
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"plain_text": f"Block {i}"}]
                    }
                }
                for i in range(101, 151)
            ],
            "has_more": False,
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(
            side_effect=[batch1, batch2]
        )

        result = await service.fetch_page_blocks("test-page-id")

        # Verify all 150 blocks are included
        assert "Block 1" in result
        assert "Block 100" in result
        assert "Block 150" in result

        # Verify pagination was used
        assert service.client.blocks.children.list.call_count == 2

        # Verify cursor was passed correctly
        call2_kwargs = service.client.blocks.children.list.call_args_list[1][1]
        assert call2_kwargs["start_cursor"] == "cursor_batch2"

    @pytest.mark.asyncio
    async def test_api_error_returns_partial_content(self):
        """API error during fetch returns partial content instead of crashing"""
        service = NotionService()

        # First batch succeeds
        batch1 = {
            "results": [
                {
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"plain_text": "First block"}]
                    }
                }
            ],
            "has_more": True,
            "next_cursor": "cursor_batch2"
        }

        # Second batch fails
        service.client.blocks.children.list = MagicMock(
            side_effect=[
                batch1,
                Exception("API Error: Rate limit exceeded")
            ]
        )

        result = await service.fetch_page_blocks("test-page-id")

        # Should return partial content, not raise exception
        assert "First block" in result
        assert result == "First block"

    @pytest.mark.asyncio
    async def test_empty_rich_text_blocks_skipped(self):
        """Blocks with empty rich_text are skipped"""
        service = NotionService()

        mock_response = {
            "results": [
                {
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"plain_text": "Real content"}]
                    }
                },
                {
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": []  # Empty
                    }
                },
                {
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"plain_text": "   "}]  # Whitespace only
                    }
                },
                {
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"plain_text": "More content"}]
                    }
                }
            ],
            "has_more": False,
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(return_value=mock_response)

        result = await service.fetch_page_blocks("test-page-id")

        # Only non-empty blocks should be included
        assert result == "Real content\n\nMore content"

    @pytest.mark.asyncio
    async def test_unsupported_block_types_ignored(self):
        """Unsupported block types are silently ignored"""
        service = NotionService()

        mock_response = {
            "results": [
                {
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"plain_text": "Known type"}]
                    }
                },
                {
                    "type": "image",  # Not yet supported
                    "image": {
                        "url": "https://example.com/image.png"
                    }
                },
                {
                    "type": "table",  # Not yet supported
                    "table": {}
                },
                {
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"plain_text": "Another known type"}]
                    }
                }
            ],
            "has_more": False,
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(return_value=mock_response)

        result = await service.fetch_page_blocks("test-page-id")

        # Only supported types should be included
        assert "Known type" in result
        assert "Another known type" in result
        assert "image" not in result.lower()
        assert "table" not in result.lower()

    @pytest.mark.asyncio
    async def test_blocks_separated_by_double_newline(self):
        """Blocks are separated by double newline"""
        service = NotionService()

        mock_response = {
            "results": [
                {
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"plain_text": "Block A"}]
                    }
                },
                {
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"plain_text": "Block B"}]
                    }
                },
                {
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"plain_text": "Block C"}]
                    }
                }
            ],
            "has_more": False,
            "next_cursor": None
        }

        service.client.blocks.children.list = MagicMock(return_value=mock_response)

        result = await service.fetch_page_blocks("test-page-id")

        assert result == "Block A\n\nBlock B\n\nBlock C"
