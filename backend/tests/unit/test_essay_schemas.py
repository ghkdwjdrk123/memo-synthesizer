"""
Unit tests for Essay Pydantic schemas validation.
"""

import pytest
from pydantic import ValidationError

from schemas.essay import EssayCreate, UsedThought


class TestUsedThought:
    """Tests for UsedThought schema validation."""

    def test_used_thought_valid(self):
        """Valid UsedThought is accepted."""
        thought = UsedThought(
            thought_id=1,
            claim="Valid claim text",
            source_title="Source Title",
            source_url="https://notion.so/page123"
        )

        assert thought.thought_id == 1
        assert thought.claim == "Valid claim text"
        assert thought.source_url == "https://notion.so/page123"

    def test_used_thought_invalid_url(self):
        """Invalid URL pattern is rejected."""
        with pytest.raises(ValidationError) as exc:
            UsedThought(
                thought_id=1,
                claim="Claim",
                source_title="Title",
                source_url="not-a-url"
            )

        assert "source_url" in str(exc.value)

    def test_used_thought_missing_fields(self):
        """Missing required fields are rejected."""
        with pytest.raises(ValidationError) as exc:
            UsedThought(
                thought_id=1,
                claim="Claim"
                # Missing source_title and source_url
            )

        assert "source_title" in str(exc.value)
        assert "source_url" in str(exc.value)


class TestEssayCreate:
    """Tests for EssayCreate schema validation."""

    def test_essay_create_valid(self):
        """Valid essay is accepted."""
        essay = EssayCreate(
            title="Valid Essay Title",
            outline=["First part", "Second part", "Third part"],
            used_thoughts=[
                UsedThought(
                    thought_id=1,
                    claim="Claim 1",
                    source_title="Source 1",
                    source_url="https://notion.so/page1"
                )
            ],
            reason="This combination creates interesting perspective",
            pair_id=1
        )

        assert essay.title == "Valid Essay Title"
        assert len(essay.outline) == 3
        assert essay.type == "essay"  # default value

    def test_essay_create_title_too_short(self):
        """Title shorter than 5 chars is rejected."""
        with pytest.raises(ValidationError) as exc:
            EssayCreate(
                title="Hi",  # Too short
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

        assert "title" in str(exc.value)

    def test_essay_create_title_too_long(self):
        """Title longer than 100 chars is rejected."""
        with pytest.raises(ValidationError) as exc:
            EssayCreate(
                title="x" * 101,  # Too long
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

        assert "title" in str(exc.value)

    def test_essay_create_outline_not_three_items(self):
        """Outline must have exactly 3 items."""
        # Too few
        with pytest.raises(ValidationError) as exc:
            EssayCreate(
                title="Valid Title",
                outline=["Only one", "Only two"],  # Only 2 items
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

        assert "outline" in str(exc.value)

        # Too many
        with pytest.raises(ValidationError) as exc:
            EssayCreate(
                title="Valid Title",
                outline=["1", "2", "3", "4"],  # 4 items
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

        assert "outline" in str(exc.value)

    def test_essay_create_no_used_thoughts(self):
        """At least one used thought is required."""
        with pytest.raises(ValidationError) as exc:
            EssayCreate(
                title="Valid Title",
                outline=["1", "2", "3"],
                used_thoughts=[],  # Empty list
                reason="Reason",
                pair_id=1
            )

        assert "used_thoughts" in str(exc.value)

    def test_essay_create_reason_too_long(self):
        """Reason longer than 300 chars is rejected."""
        with pytest.raises(ValidationError) as exc:
            EssayCreate(
                title="Valid Title",
                outline=["1", "2", "3"],
                used_thoughts=[
                    UsedThought(
                        thought_id=1,
                        claim="Claim",
                        source_title="Title",
                        source_url="https://test.com"
                    )
                ],
                reason="x" * 301,  # Too long
                pair_id=1
            )

        assert "reason" in str(exc.value)

    def test_essay_create_multiple_used_thoughts(self):
        """Multiple used thoughts are accepted."""
        essay = EssayCreate(
            title="Valid Title",
            outline=["1", "2", "3"],
            used_thoughts=[
                UsedThought(
                    thought_id=1,
                    claim="Claim 1",
                    source_title="Title 1",
                    source_url="https://test1.com"
                ),
                UsedThought(
                    thought_id=2,
                    claim="Claim 2",
                    source_title="Title 2",
                    source_url="https://test2.com"
                )
            ],
            reason="Reason",
            pair_id=1
        )

        assert len(essay.used_thoughts) == 2

    def test_essay_create_custom_type(self):
        """Custom type field is accepted."""
        essay = EssayCreate(
            type="article",  # Custom type
            title="Valid Title",
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

        assert essay.type == "article"

    def test_essay_create_missing_pair_id(self):
        """Missing pair_id is rejected."""
        with pytest.raises(ValidationError) as exc:
            EssayCreate(
                title="Valid Title",
                outline=["1", "2", "3"],
                used_thoughts=[
                    UsedThought(
                        thought_id=1,
                        claim="Claim",
                        source_title="Title",
                        source_url="https://test.com"
                    )
                ],
                reason="Reason"
                # Missing pair_id
            )

        assert "pair_id" in str(exc.value)
