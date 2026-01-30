"""
Distance Table API 통합 테스트.

테스트 범위:
- POST /pipeline/distance-table/build: 초기 구축 엔드포인트
- GET /pipeline/distance-table/status: 상태 조회 엔드포인트
- POST /pipeline/distance-table/update: 증분 갱신 엔드포인트
- POST /pipeline/collect-candidates (use_distance_table=True/False)
- POST /pipeline/extract-thoughts (auto_update_distance_table=True)
"""

import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock, patch

from main import app


@pytest.fixture
def mock_distance_service():
    """Mock DistanceTableService for API tests."""
    service = MagicMock()

    # Default mock responses
    service.build_distance_table_batched = AsyncMock(
        return_value={
            "success": True,
            "total_pairs": 1846210,
            "total_thoughts": 1921,
            "duration_seconds": 420,
            "batch_size": 50
        }
    )

    service.get_statistics = AsyncMock(
        return_value={
            "total_pairs": 1846210,
            "min_similarity": 0.001,
            "max_similarity": 0.999,
            "avg_similarity": 0.423
        }
    )

    service.update_distance_table_incremental = AsyncMock(
        return_value={
            "success": True,
            "new_thought_count": 10,
            "new_pairs_inserted": 19210
        }
    )

    with patch("routers.pipeline.get_distance_table_service", return_value=service):
        yield service


@pytest.fixture
def mock_supabase_service():
    """Mock SupabaseService for API tests."""
    service = MagicMock()

    # Mock get_candidates_from_distance_table
    service.get_candidates_from_distance_table = AsyncMock(
        return_value=[
            {
                "thought_a_id": 1,
                "thought_b_id": 2,
                "thought_a_claim": "사고 A",
                "thought_b_claim": "사고 B",
                "similarity": 0.15,
                "raw_note_id_a": "note-a",
                "raw_note_id_b": "note-b"
            }
        ]
    )

    # Mock find_candidate_pairs (v4 fallback)
    service.find_candidate_pairs = AsyncMock(
        return_value=[
            {
                "thought_a_id": 1,
                "thought_b_id": 2,
                "thought_a_claim": "사고 A",
                "thought_b_claim": "사고 B",
                "similarity_score": 0.15,
                "raw_note_id_a": "note-a",
                "raw_note_id_b": "note-b"
            }
        ]
    )

    # Mock insert_pair_candidates_batch
    from schemas.zk import PairCandidateBatch
    service.insert_pair_candidates_batch = AsyncMock(
        return_value=PairCandidateBatch(
            inserted_count=1,
            duplicate_count=0,
            error_count=0
        )
    )

    # Mock get_raw_note_ids
    service.get_raw_note_ids = AsyncMock(return_value=[])

    # Mock insert_thought_units_batch
    service.insert_thought_units_batch = AsyncMock(return_value=[])

    with patch("routers.pipeline.get_supabase_service", return_value=service):
        yield service


@pytest.fixture
def mock_ai_service():
    """Mock AIService for API tests."""
    service = MagicMock()

    with patch("routers.pipeline.get_ai_service", return_value=service):
        yield service


class TestBuildDistanceTableEndpoint:
    """POST /pipeline/distance-table/build 테스트"""

    @pytest.mark.asyncio
    async def test_build_distance_table_endpoint_success(self, mock_distance_service):
        """초기 구축 엔드포인트 호출 성공"""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/pipeline/distance-table/build?batch_size=50"
            )

        # Assertions
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "Distance table build started" in data["message"]
        assert "batch_size=50" in data["message"]

    @pytest.mark.asyncio
    async def test_build_distance_table_endpoint_custom_batch_size(
        self, mock_distance_service
    ):
        """커스텀 batch_size"""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/pipeline/distance-table/build?batch_size=100"
            )

        assert response.status_code == 200
        data = response.json()
        assert "batch_size=100" in data["message"]


class TestGetDistanceTableStatusEndpoint:
    """GET /pipeline/distance-table/status 테스트"""

    @pytest.mark.asyncio
    async def test_get_status_endpoint_success(self, mock_distance_service):
        """상태 조회 성공"""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/pipeline/distance-table/status")

        # Assertions
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "statistics" in data
        assert data["statistics"]["total_pairs"] == 1846210
        assert data["statistics"]["min_similarity"] == 0.001
        assert data["statistics"]["max_similarity"] == 0.999
        assert data["statistics"]["avg_similarity"] == 0.423

    @pytest.mark.asyncio
    async def test_get_status_endpoint_empty_table(self, mock_distance_service):
        """빈 테이블 상태"""
        # Mock empty statistics
        mock_distance_service.get_statistics = AsyncMock(
            return_value={
                "total_pairs": 0,
                "min_similarity": None,
                "max_similarity": None,
                "avg_similarity": None
            }
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/pipeline/distance-table/status")

        assert response.status_code == 200
        data = response.json()
        assert data["statistics"]["total_pairs"] == 0
        assert data["statistics"]["min_similarity"] is None


class TestUpdateDistanceTableEndpoint:
    """POST /pipeline/distance-table/update 테스트"""

    @pytest.mark.asyncio
    async def test_update_distance_table_endpoint_auto_detect(
        self, mock_distance_service
    ):
        """자동 감지 모드"""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/pipeline/distance-table/update")

        # Assertions
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["new_thought_count"] == 10
        assert data["new_pairs_inserted"] == 19210

        # RPC 호출 검증 (auto-detect: None)
        mock_distance_service.update_distance_table_incremental.assert_called_once_with(
            None
        )

    @pytest.mark.asyncio
    async def test_update_distance_table_endpoint_manual(self, mock_distance_service):
        """수동 지정 모드"""
        # Mock manual mode
        mock_distance_service.update_distance_table_incremental = AsyncMock(
            return_value={
                "success": True,
                "new_thought_count": 3,
                "new_pairs_inserted": 5763
            }
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/pipeline/distance-table/update?new_thought_ids=1&new_thought_ids=2&new_thought_ids=3"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["new_thought_count"] == 3
        assert data["new_pairs_inserted"] == 5763


class TestCollectCandidatesWithDistanceTable:
    """POST /pipeline/collect-candidates 테스트 (Distance Table vs v4 fallback)"""

    @pytest.mark.asyncio
    async def test_collect_candidates_with_distance_table(
        self, mock_supabase_service
    ):
        """Distance Table 사용 (빠른 조회)"""
        # Mock distribution service (import는 함수 내부에서 발생)
        with patch("services.distribution_service.DistributionService") as mock_dist:
            dist_service = MagicMock()
            dist_service.get_relative_thresholds = AsyncMock(
                return_value=(0.10, 0.40)
            )
            mock_dist.return_value = dist_service

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/pipeline/collect-candidates?use_distance_table=true&strategy=p10_p40"
                )

        # Assertions
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["query_method"] == "distance_table"
        assert data["total_candidates"] == 1
        assert data["inserted"] == 1

        # Distance Table 조회 호출 검증
        mock_supabase_service.get_candidates_from_distance_table.assert_called_once()

    @pytest.mark.asyncio
    async def test_collect_candidates_with_v4_fallback(self, mock_supabase_service):
        """v4 fallback 사용 (느린 조회)"""
        # Mock distribution service
        with patch("services.distribution_service.DistributionService") as mock_dist:
            dist_service = MagicMock()
            dist_service.get_relative_thresholds = AsyncMock(
                return_value=(0.10, 0.40)
            )
            mock_dist.return_value = dist_service

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/pipeline/collect-candidates?use_distance_table=false&strategy=p10_p40"
                )

        # Assertions
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["query_method"] == "v4_fallback"

        # v4 find_candidate_pairs 호출 검증
        mock_supabase_service.find_candidate_pairs.assert_called_once()


class TestExtractThoughtsAutoUpdate:
    """POST /pipeline/extract-thoughts (auto_update_distance_table=True) 테스트"""

    @pytest.mark.asyncio
    async def test_extract_thoughts_auto_update_enabled(
        self, mock_supabase_service, mock_ai_service, mock_distance_service
    ):
        """자동 갱신 활성화 (10개 이상 추출)"""
        # Mock 10개 이상 추출
        mock_supabase_service.get_raw_note_ids = AsyncMock(return_value=["note-1"])
        mock_supabase_service.get_raw_notes_by_ids = AsyncMock(
            return_value=[
                {
                    "id": "note-1",
                    "title": "테스트 메모",
                    "properties_json": {"본문": "메모 내용"}
                }
            ]
        )

        # Mock AI extraction (10개 thoughts)
        from schemas.normalized import ThoughtExtractionResult, ThoughtUnit
        mock_ai_service.extract_thoughts = AsyncMock(
            return_value=ThoughtExtractionResult(
                thoughts=[
                    ThoughtUnit(claim=f"이것은 테스트 사고 단위 번호 {i}입니다", context=None)
                    for i in range(10)
                ]
            )
        )

        # Mock embedding
        mock_ai_service.create_embedding = AsyncMock(
            return_value={"success": True, "embedding": [0.1] * 1536}
        )

        # Mock insert
        mock_supabase_service.insert_thought_units_batch = AsyncMock(
            return_value=[{"id": i} for i in range(10)]
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/pipeline/extract-thoughts?auto_update_distance_table=true"
            )

        # Assertions
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["total_thoughts"] == 10
        assert data["distance_table_updated"] is True
        assert "distance_table_result" in data

        # Distance Table update 호출 검증
        mock_distance_service.update_distance_table_incremental.assert_called_once()

    @pytest.mark.asyncio
    async def test_extract_thoughts_auto_update_disabled(
        self, mock_supabase_service, mock_ai_service, mock_distance_service
    ):
        """자동 갱신 비활성화"""
        # Mock
        mock_supabase_service.get_raw_note_ids = AsyncMock(return_value=[])

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/pipeline/extract-thoughts?auto_update_distance_table=false"
            )

        # Assertions
        assert response.status_code == 200
        data = response.json()
        assert data["distance_table_updated"] is False

        # Distance Table update는 호출되지 않아야 함
        mock_distance_service.update_distance_table_incremental.assert_not_called()

    @pytest.mark.asyncio
    async def test_extract_thoughts_auto_update_insufficient_thoughts(
        self, mock_supabase_service, mock_ai_service, mock_distance_service
    ):
        """10개 미만 추출 시 자동 갱신 스킵"""
        # Mock 5개만 추출
        mock_supabase_service.get_raw_note_ids = AsyncMock(return_value=["note-1"])
        mock_supabase_service.get_raw_notes_by_ids = AsyncMock(
            return_value=[
                {
                    "id": "note-1",
                    "title": "테스트",
                    "properties_json": {"본문": "내용"}
                }
            ]
        )

        from schemas.normalized import ThoughtExtractionResult, ThoughtUnit
        mock_ai_service.extract_thoughts = AsyncMock(
            return_value=ThoughtExtractionResult(
                thoughts=[
                    ThoughtUnit(claim=f"이것은 테스트 사고 단위 번호 {i}입니다", context=None)
                    for i in range(5)
                ]
            )
        )

        mock_ai_service.create_embedding = AsyncMock(
            return_value={"success": True, "embedding": [0.1] * 1536}
        )

        mock_supabase_service.insert_thought_units_batch = AsyncMock(
            return_value=[{"id": i} for i in range(5)]
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/pipeline/extract-thoughts?auto_update_distance_table=true"
            )

        # Assertions
        assert response.status_code == 200
        data = response.json()
        assert data["total_thoughts"] == 5
        assert data["distance_table_updated"] is False

        # Distance Table update는 호출되지 않아야 함
        mock_distance_service.update_distance_table_incremental.assert_not_called()
