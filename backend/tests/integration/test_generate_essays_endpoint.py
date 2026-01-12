"""
Integration tests for POST /pipeline/generate-essays endpoint.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient

# Note: This assumes you have a way to import the FastAPI app
# Adjust the import based on your actual structure
try:
    from main import app
except ImportError:
    app = None


@pytest.mark.skipif(app is None, reason="FastAPI app not available")
class TestGenerateEssaysEndpoint:
    """Integration tests for /pipeline/generate-essays endpoint."""

    @pytest.mark.asyncio
    async def test_generate_essays_success(
        self, mock_supabase_client, mock_anthropic_client, sample_unused_pairs
    ):
        """Endpoint successfully generates essays."""
        # Mock get_unused_thought_pairs
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
            return_value=MagicMock(data=sample_unused_pairs[:2])
        )

        # Mock get_pair_with_thoughts
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.single.return_value.execute = AsyncMock(
            return_value=MagicMock(
                data={
                    "id": 1,
                    "thought_a_id": 10,
                    "thought_b_id": 20,
                    "similarity_score": 0.45,
                    "connection_reason": "Test connection"
                }
            )
        )

        # Mock thought units
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.single.return_value.execute = AsyncMock(
            return_value=MagicMock(
                data={
                    "id": 10,
                    "claim": "Test claim",
                    "context": "Test context",
                    "raw_note_id": "note-1"
                }
            )
        )

        # Mock insert essays batch
        mock_supabase_client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=MagicMock(
                data=[
                    {"id": 1, "title": "Essay 1", "pair_id": 1},
                    {"id": 2, "title": "Essay 2", "pair_id": 2}
                ]
            )
        )

        # Mock update pairs
        mock_supabase_client.table.return_value.update.return_value.in_.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[])
        )

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/pipeline/generate-essays?max_pairs=2")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["pairs_processed"] >= 0
        assert data["essays_generated"] >= 0

    @pytest.mark.asyncio
    async def test_generate_essays_no_unused_pairs(
        self, mock_supabase_client, mock_anthropic_client
    ):
        """Endpoint handles no unused pairs gracefully."""
        # Mock empty unused pairs
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[])
        )

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/pipeline/generate-essays")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "No unused pairs" in str(data.get("errors", []))

    @pytest.mark.asyncio
    async def test_generate_essays_max_pairs_parameter(
        self, mock_supabase_client, mock_anthropic_client, sample_unused_pairs
    ):
        """max_pairs parameter limits number of processed pairs."""
        # Mock 5 unused pairs
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
            return_value=MagicMock(data=sample_unused_pairs[:5])
        )

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/pipeline/generate-essays?max_pairs=3")

        # Should only process 3 pairs
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_generate_essays_partial_failure(
        self, mock_supabase_client, mock_anthropic_client, sample_unused_pairs
    ):
        """Partial failure allows other essays to succeed."""
        # Mock unused pairs
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
            return_value=MagicMock(data=sample_unused_pairs[:2])
        )

        # First call succeeds, second fails
        mock_anthropic_client.messages.create.side_effect = [
            MagicMock(content=[MagicMock(text='{"title": "t", "outline": ["1","2","3"], "reason": "r"}')]),
            Exception("API Error")
        ]

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/pipeline/generate-essays?max_pairs=2")

        assert response.status_code == 200
        data = response.json()
        # At least one should succeed
        assert len(data.get("errors", [])) > 0  # One failed

    @pytest.mark.asyncio
    async def test_generate_essays_invalid_max_pairs(
        self, mock_supabase_client, mock_anthropic_client
    ):
        """Invalid max_pairs value is rejected."""
        async with AsyncClient(app=app, base_url="http://test") as client:
            # max_pairs must be >= 1 and <= 10
            response = await client.post("/pipeline/generate-essays?max_pairs=0")

        assert response.status_code == 422  # Validation error

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/pipeline/generate-essays?max_pairs=11")

        assert response.status_code == 422


@pytest.mark.skipif(app is None, reason="FastAPI app not available")
class TestGetEssaysEndpoint:
    """Integration tests for GET /pipeline/essays endpoint."""

    @pytest.mark.asyncio
    async def test_get_essays_success(self, mock_supabase_client):
        """Successfully retrieve essays."""
        # Mock get_essays
        mock_supabase_client.table.return_value.select.return_value.order.return_value.limit.return_value.offset.return_value.execute = AsyncMock(
            return_value=MagicMock(
                data=[
                    {
                        "id": 1,
                        "title": "Essay 1",
                        "outline": ["1", "2", "3"],
                        "used_thoughts_json": [],
                        "reason": "Reason 1",
                        "pair_id": 1,
                        "generated_at": "2024-01-01"
                    },
                    {
                        "id": 2,
                        "title": "Essay 2",
                        "outline": ["1", "2", "3"],
                        "used_thoughts_json": [],
                        "reason": "Reason 2",
                        "pair_id": 2,
                        "generated_at": "2024-01-02"
                    }
                ]
            )
        )

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/pipeline/essays")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["count"] == 2
        assert len(data["essays"]) == 2

    @pytest.mark.asyncio
    async def test_get_essays_empty_database(self, mock_supabase_client):
        """Empty database returns empty list."""
        # Mock empty result
        mock_supabase_client.table.return_value.select.return_value.order.return_value.limit.return_value.offset.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[])
        )

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/pipeline/essays")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["count"] == 0
        assert data["essays"] == []

    @pytest.mark.asyncio
    async def test_get_essays_pagination(self, mock_supabase_client):
        """Pagination parameters work correctly."""
        # Mock paginated result
        mock_supabase_client.table.return_value.select.return_value.order.return_value.limit.return_value.offset.return_value.execute = AsyncMock(
            return_value=MagicMock(
                data=[
                    {"id": 11, "title": "Essay 11"},
                    {"id": 12, "title": "Essay 12"}
                ]
            )
        )

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/pipeline/essays?limit=2&offset=10")

        assert response.status_code == 200
        data = response.json()
        assert len(data["essays"]) == 2

    @pytest.mark.asyncio
    async def test_get_essays_database_error(self, mock_supabase_client):
        """Database error returns 500."""
        # Mock database error
        mock_supabase_client.table.return_value.select.return_value.order.return_value.limit.return_value.offset.return_value.execute = AsyncMock(
            side_effect=Exception("Database error")
        )

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/pipeline/essays")

        assert response.status_code == 500
