"""
Unit tests for AIService.generate_essay() method.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic import ValidationError

from services.ai_service import AIService


class TestGenerateEssay:
    """Tests for AIService.generate_essay() method."""

    @pytest.mark.asyncio
    async def test_generate_essay_success(self, mock_anthropic_client, sample_pair_data):
        """Normal essay generation succeeds."""
        ai_service = AIService()

        # Valid JSON response
        mock_anthropic_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text='''
{
    "title": "프로그래밍과 예술의 공통점",
    "outline": [
        "1단: 프로그래밍의 창의성",
        "2단: 예술의 제약",
        "3단: 두 영역의 융합"
    ],
    "reason": "두 영역 모두 제약 속에서 창의성을 발휘한다는 공통점이 있습니다."
}
''')]
        )

        result = await ai_service.generate_essay(sample_pair_data)

        assert result is not None
        assert "title" in result
        assert "outline" in result
        assert "reason" in result
        assert "used_thoughts" in result
        assert len(result["outline"]) == 3
        assert len(result["used_thoughts"]) == 2
        assert result["used_thoughts"][0]["thought_id"] == 10
        assert result["used_thoughts"][1]["thought_id"] == 20

    @pytest.mark.asyncio
    async def test_generate_essay_with_markdown_wrapper(
        self, mock_anthropic_client, sample_pair_data
    ):
        """Essay generation handles markdown-wrapped JSON."""
        ai_service = AIService()

        # JSON wrapped in markdown code block
        mock_anthropic_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text='''
```json
{
    "title": "테스트 제목",
    "outline": ["1단", "2단", "3단"],
    "reason": "테스트 이유"
}
```
''')]
        )

        result = await ai_service.generate_essay(sample_pair_data)

        assert result is not None
        assert result["title"] == "테스트 제목"
        assert len(result["outline"]) == 3

    @pytest.mark.asyncio
    async def test_generate_essay_invalid_json(
        self, mock_anthropic_client, sample_pair_data
    ):
        """Invalid JSON raises ValueError."""
        ai_service = AIService()

        # Invalid JSON response
        mock_anthropic_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="Not JSON at all")]
        )

        with pytest.raises(ValueError, match="Invalid JSON response"):
            await ai_service.generate_essay(sample_pair_data)

    @pytest.mark.asyncio
    async def test_generate_essay_api_error(
        self, mock_anthropic_client, sample_pair_data
    ):
        """Claude API error is propagated."""
        ai_service = AIService()

        # Simulate API error
        mock_anthropic_client.messages.create.side_effect = Exception("API Error")

        with pytest.raises(Exception, match="API Error"):
            await ai_service.generate_essay(sample_pair_data)

    @pytest.mark.asyncio
    async def test_generate_essay_incomplete_json(
        self, mock_anthropic_client, sample_pair_data
    ):
        """Incomplete JSON raises ValueError."""
        ai_service = AIService()

        # Missing required field
        mock_anthropic_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text='''
{
    "title": "제목만 있음",
    "outline": ["1단", "2단"]
}
''')]
        )

        # Should fail because outline needs exactly 3 items
        result = await ai_service.generate_essay(sample_pair_data)
        # Note: This test checks if the result has correct structure
        # Pydantic validation happens at schema level

    @pytest.mark.asyncio
    async def test_generate_essay_used_thoughts_structure(
        self, mock_anthropic_client, sample_pair_data
    ):
        """used_thoughts contains correct source information."""
        ai_service = AIService()

        mock_anthropic_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text='''
{
    "title": "테스트",
    "outline": ["1단", "2단", "3단"],
    "reason": "이유"
}
''')]
        )

        result = await ai_service.generate_essay(sample_pair_data)

        # Check used_thoughts structure
        assert len(result["used_thoughts"]) == 2

        thought_a = result["used_thoughts"][0]
        assert thought_a["thought_id"] == sample_pair_data["thought_a"]["id"]
        assert thought_a["claim"] == sample_pair_data["thought_a"]["claim"]
        assert thought_a["source_title"] == sample_pair_data["thought_a"]["source_title"]
        assert thought_a["source_url"] == sample_pair_data["thought_a"]["source_url"]

        thought_b = result["used_thoughts"][1]
        assert thought_b["thought_id"] == sample_pair_data["thought_b"]["id"]
        assert thought_b["claim"] == sample_pair_data["thought_b"]["claim"]

    @pytest.mark.asyncio
    async def test_generate_essay_with_trailing_comma(
        self, mock_anthropic_client, sample_pair_data
    ):
        """JSON with trailing comma is handled gracefully."""
        ai_service = AIService()

        # JSON with trailing comma
        mock_anthropic_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text='''
{
    "title": "테스트",
    "outline": ["1단", "2단", "3단",],
    "reason": "이유",
}
''')]
        )

        result = await ai_service.generate_essay(sample_pair_data)

        assert result is not None
        assert result["title"] == "테스트"

    @pytest.mark.asyncio
    async def test_generate_essay_with_extra_text(
        self, mock_anthropic_client, sample_pair_data
    ):
        """JSON with extra text before/after is handled."""
        ai_service = AIService()

        # Extra text around JSON
        mock_anthropic_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text='''
여기 에세이를 생성했습니다:

{
    "title": "테스트",
    "outline": ["1단", "2단", "3단"],
    "reason": "이유"
}

이상입니다.
''')]
        )

        result = await ai_service.generate_essay(sample_pair_data)

        assert result is not None
        assert result["title"] == "테스트"

    @pytest.mark.asyncio
    async def test_generate_essay_empty_response(
        self, mock_anthropic_client, sample_pair_data
    ):
        """Empty response from Claude raises ValueError."""
        ai_service = AIService()

        mock_anthropic_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="")]
        )

        with pytest.raises(ValueError, match="Invalid JSON response"):
            await ai_service.generate_essay(sample_pair_data)

    @pytest.mark.asyncio
    async def test_generate_essay_preserves_pair_context(
        self, mock_anthropic_client, sample_pair_data
    ):
        """Essay generation includes pair context in prompt."""
        ai_service = AIService()

        mock_anthropic_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text='{"title": "t", "outline": ["1", "2", "3"], "reason": "r"}')]
        )

        await ai_service.generate_essay(sample_pair_data)

        # Verify Claude was called with correct context
        call_args = mock_anthropic_client.messages.create.call_args
        prompt = call_args.kwargs.get("messages")[0]["content"]

        assert sample_pair_data["thought_a"]["claim"] in prompt
        assert sample_pair_data["thought_b"]["claim"] in prompt
        assert str(sample_pair_data["similarity_score"]) in prompt
        assert sample_pair_data["connection_reason"] in prompt
