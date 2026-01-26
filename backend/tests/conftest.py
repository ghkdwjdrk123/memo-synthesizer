"""
Pytest configuration and shared fixtures for Step 4 (Essay generation) tests.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_supabase_client():
    """Mock Supabase client for database operations."""
    with patch('services.supabase_service.create_async_client') as mock:
        client = MagicMock()

        # Default responses
        client.table.return_value.select.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[])
        )
        client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[{"id": 1}])
        )
        client.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[{"id": 1}])
        )

        mock.return_value = AsyncMock(return_value=client)
        yield client


@pytest.fixture
def mock_anthropic_client():
    """Mock Anthropic client for Claude API calls."""
    with patch('services.ai_service.Anthropic') as mock:
        client = MagicMock()

        # Default essay response
        client.messages.create.return_value = MagicMock(
            content=[MagicMock(text='''
{
    "title": "테스트 에세이 제목",
    "outline": [
        "1단: 첫 번째 아이디어 소개",
        "2단: 두 번째 아이디어와 연결",
        "3단: 통합된 통찰"
    ],
    "reason": "두 아이디어의 흥미로운 조합을 통해 새로운 시각을 제공합니다."
}
''')]
        )

        mock.return_value = client
        yield client


@pytest.fixture
def sample_pair_data():
    """Sample thought pair data for testing."""
    return {
        "pair_id": 1,
        "similarity_score": 0.45,
        "connection_reason": "두 아이디어는 서로 다른 관점에서 같은 주제를 다룹니다.",
        "thought_a": {
            "id": 10,
            "claim": "프로그래밍은 창의적인 문제 해결 과정이다",
            "context": "소프트웨어 개발",
            "source_title": "프로그래밍의 본질",
            "source_url": "https://notion.so/test-page-a"
        },
        "thought_b": {
            "id": 20,
            "claim": "예술은 제약 속에서 피어난다",
            "context": "창작 활동",
            "source_title": "예술과 제약",
            "source_url": "https://notion.so/test-page-b"
        }
    }


@pytest.fixture
def sample_unused_pairs():
    """Sample unused thought pairs from database."""
    return [
        {
            "id": 1,
            "thought_a_id": 10,
            "thought_b_id": 20,
            "similarity_score": 0.45,
            "connection_reason": "Test connection",
            "is_used_in_essay": False
        },
        {
            "id": 2,
            "thought_a_id": 11,
            "thought_b_id": 21,
            "similarity_score": 0.52,
            "connection_reason": "Another connection",
            "is_used_in_essay": False
        }
    ]


@pytest.fixture
def sample_essay_dict():
    """Sample essay dictionary from AI service."""
    return {
        "title": "프로그래밍과 예술: 제약 속의 창의성",
        "outline": [
            "1단: 프로그래밍에서의 창의적 문제 해결 과정 탐구",
            "2단: 예술 창작에서 제약이 주는 긍정적 영향 분석",
            "3단: 두 영역의 공통점을 통한 창의성의 본질 고찰"
        ],
        "reason": "서로 다른 영역에서 창의성이 발현되는 메커니즘의 유사성을 발견할 수 있습니다.",
        "used_thoughts": [
            {
                "thought_id": 10,
                "claim": "프로그래밍은 창의적인 문제 해결 과정이다",
                "source_title": "프로그래밍의 본질",
                "source_url": "https://notion.so/test-page-a"
            },
            {
                "thought_id": 20,
                "claim": "예술은 제약 속에서 피어난다",
                "source_title": "예술과 제약",
                "source_url": "https://notion.so/test-page-b"
            }
        ]
    }


@pytest.fixture
def sample_saved_essay():
    """Sample saved essay from database."""
    return {
        "id": 1,
        "type": "essay",
        "title": "프로그래밍과 예술: 제약 속의 창의성",
        "outline": [
            "1단: 프로그래밍에서의 창의적 문제 해결 과정 탐구",
            "2단: 예술 창작에서 제약이 주는 긍정적 영향 분석",
            "3단: 두 영역의 공통점을 통한 창의성의 본질 고찰"
        ],
        "used_thoughts_json": [
            {
                "thought_id": 10,
                "claim": "프로그래밍은 창의적인 문제 해결 과정이다",
                "source_title": "프로그래밍의 본질",
                "source_url": "https://notion.so/test-page-a"
            },
            {
                "thought_id": 20,
                "claim": "예술은 제약 속에서 피어난다",
                "source_title": "예술과 제약",
                "source_url": "https://notion.so/test-page-b"
            }
        ],
        "reason": "서로 다른 영역에서 창의성이 발현되는 메커니즘의 유사성을 발견할 수 있습니다.",
        "pair_id": 1,
        "generated_at": datetime.now().isoformat()
    }


# ============================================================
# 하이브리드 C 전략 Fixtures
# ============================================================

@pytest.fixture
def mock_supabase_hybrid():
    """Mock Supabase client for hybrid strategy tests."""
    with patch('services.supabase_service.create_async_client') as mock:
        client = MagicMock()

        # pair_candidates 테이블 mock
        client.table.return_value.upsert.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[{"id": i} for i in range(100)])
        )
        client.table.return_value.select.return_value.eq.return_value.lt.return_value.gte.return_value.lte.return_value.order.return_value.limit.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[])
        )

        # thought_pairs 테이블 mock
        client.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[{"id": 1}])
        )

        mock.return_value = AsyncMock(return_value=client)
        yield client


@pytest.fixture
def mock_ai_service_hybrid():
    """Mock AI service for hybrid strategy tests."""
    with patch('services.ai_service.Anthropic') as mock:
        client = MagicMock()

        # score_pairs 응답 mock
        client.messages.create.return_value = MagicMock(
            content=[MagicMock(text='''
{
    "pair_scores": [
        {
            "thought_a_id": 10,
            "thought_b_id": 20,
            "logical_expansion_score": 85,
            "connection_reason": "두 아이디어는 창의적으로 연결됩니다."
        }
    ]
}
''')]
        )

        mock.return_value = client
        yield client


@pytest.fixture
def sample_candidates():
    """Sample pair candidates for testing (1000개)."""
    return [
        {
            'id': i,
            'thought_a_id': i * 2,
            'thought_b_id': i * 2 + 1,
            'similarity': 0.05 + (i % 30) * 0.01,  # 0.05 ~ 0.34 범위
            'raw_note_id_a': f'note-{i % 100}',
            'raw_note_id_b': f'note-{(i % 100) + 1}'
        }
        for i in range(1, 1001)
    ]


@pytest.fixture
def sample_pending_candidates():
    """Sample pending candidates with thought claims."""
    return [
        {
            'id': i,
            'thought_a_id': i * 2,
            'thought_b_id': i * 2 + 1,
            'thought_a_claim': f'사고 A {i}: 창의적인 아이디어',
            'thought_b_claim': f'사고 B {i}: 다른 관점의 통찰',
            'similarity': 0.2,
            'raw_note_id_a': f'note-{i}',
            'raw_note_id_b': f'note-{i+1}'
        }
        for i in range(1, 51)
    ]


@pytest.fixture
def sample_scored_pairs():
    """Sample scored pairs from Claude evaluation."""
    from schemas.zk import PairScore
    return [
        PairScore(
            thought_a_id=i * 2,
            thought_b_id=i * 2 + 1,
            logical_expansion_score=50 + i * 5,  # 55 ~ 95 범위
            connection_reason=f'연결 이유 {i}: 두 사고는 상호보완적입니다.'
        )
        for i in range(1, 11)
    ]
