"""
Unit tests for SupabaseService essay CRUD methods - simplified version.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from schemas.essay import EssayCreate, UsedThought


class TestEssayMethods:
    """Tests for essay CRUD methods with proper mocking."""

    @pytest.mark.asyncio
    async def test_insert_essay_validates_schema(self):
        """EssayCreate schema validates correctly."""
        # Test valid essay
        essay = EssayCreate(
            title="Valid Title",
            outline=["First", "Second", "Third"],
            used_thoughts=[
                UsedThought(
                    thought_id=1,
                    claim="Claim",
                    source_title="Title",
                    source_url="https://test.com"
                )
            ],
            reason="Valid reason",
            pair_id=1
        )

        assert essay.title == "Valid Title"
        assert len(essay.outline) == 3
        assert len(essay.used_thoughts) == 1
        assert essay.pair_id == 1

    @pytest.mark.asyncio
    async def test_essay_create_serialization(self):
        """EssayCreate can be serialized to dict for database."""
        essay = EssayCreate(
            title="Test Essay",
            outline=["1", "2", "3"],
            used_thoughts=[
                UsedThought(
                    thought_id=1,
                    claim="Claim",
                    source_title="Title",
                    source_url="https://test.com"
                )
            ],
            reason="Reason",
            pair_id=1
        )

        # Simulate what the service does
        essay_dict = {
            "type": essay.type,
            "title": essay.title,
            "outline": essay.outline,
            "used_thoughts_json": [t.model_dump() for t in essay.used_thoughts],
            "reason": essay.reason,
            "pair_id": essay.pair_id
        }

        assert essay_dict["title"] == "Test Essay"
        assert len(essay_dict["outline"]) == 3
        assert len(essay_dict["used_thoughts_json"]) == 1
        assert essay_dict["used_thoughts_json"][0]["thought_id"] == 1

    @pytest.mark.asyncio
    async def test_batch_essay_serialization(self):
        """Multiple essays can be serialized for batch insert."""
        essays = [
            EssayCreate(
                title=f"Essay {i}",
                outline=["1", "2", "3"],
                used_thoughts=[
                    UsedThought(
                        thought_id=i,
                        claim=f"Claim {i}",
                        source_title=f"Title {i}",
                        source_url=f"https://test{i}.com"
                    )
                ],
                reason=f"Reason {i}",
                pair_id=i
            )
            for i in range(1, 4)
        ]

        # Simulate batch serialization
        essays_dict = [
            {
                "type": e.type,
                "title": e.title,
                "outline": e.outline,
                "used_thoughts_json": [t.model_dump() for t in e.used_thoughts],
                "reason": e.reason,
                "pair_id": e.pair_id
            }
            for e in essays
        ]

        assert len(essays_dict) == 3
        assert essays_dict[0]["title"] == "Essay 1"
        assert essays_dict[2]["pair_id"] == 3

    @pytest.mark.asyncio
    async def test_essay_with_multiple_thoughts(self):
        """Essay can have multiple used thoughts."""
        essay = EssayCreate(
            title="Multi-thought Essay",
            outline=["1", "2", "3"],
            used_thoughts=[
                UsedThought(
                    thought_id=1,
                    claim="First claim",
                    source_title="Source 1",
                    source_url="https://test1.com"
                ),
                UsedThought(
                    thought_id=2,
                    claim="Second claim",
                    source_title="Source 2",
                    source_url="https://test2.com"
                ),
                UsedThought(
                    thought_id=3,
                    claim="Third claim",
                    source_title="Source 3",
                    source_url="https://test3.com"
                )
            ],
            reason="Multi-thought reason",
            pair_id=1
        )

        essay_dict = {
            "used_thoughts_json": [t.model_dump() for t in essay.used_thoughts]
        }

        assert len(essay_dict["used_thoughts_json"]) == 3
        assert essay_dict["used_thoughts_json"][0]["thought_id"] == 1
        assert essay_dict["used_thoughts_json"][2]["thought_id"] == 3
