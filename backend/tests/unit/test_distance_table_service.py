"""
Distance Table Service 유닛 테스트.

테스트 범위:
- build_distance_table_batched: 초기 구축 로직
- update_distance_table_incremental: 증분 갱신 로직
- get_statistics: 통계 조회
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from services.distance_table_service import DistanceTableService


@pytest.fixture
def mock_supabase_service():
    """Mock Supabase service for DistanceTableService tests."""
    service = MagicMock()
    service._ensure_initialized = AsyncMock()
    service.client = MagicMock()
    return service


@pytest.fixture
def distance_service(mock_supabase_service):
    """DistanceTableService fixture with mocked supabase."""
    return DistanceTableService(supabase_service=mock_supabase_service)


class TestBuildDistanceTableBatched:
    """build_distance_table_batched() 테스트"""

    @pytest.mark.asyncio
    async def test_build_distance_table_batched_success(
        self, distance_service, mock_supabase_service
    ):
        """초기 구축 성공 케이스"""
        # Mock count response (total thoughts)
        count_response = MagicMock()
        count_response.count = 100
        mock_supabase_service.client.table.return_value.select.return_value.not_.is_.return_value.execute = AsyncMock(
            return_value=count_response
        )

        # Mock truncate
        mock_supabase_service.client.table.return_value.delete.return_value.neq.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[])
        )

        # Mock RPC calls (2 batches: 0-50, 50-100)
        # RPC는 체인형으로 호출되므로 execute()를 반환하는 mock 생성
        rpc_responses = []
        for _ in range(2):
            rpc_mock = MagicMock()
            rpc_mock.execute = AsyncMock(return_value=MagicMock(data={"pairs_inserted": 1225}))
            rpc_responses.append(rpc_mock)

        mock_supabase_service.client.rpc = MagicMock(side_effect=rpc_responses)

        # Execute
        result = await distance_service.build_distance_table_batched(batch_size=50)

        # Assertions
        assert result["success"] is True
        assert result["total_pairs"] == 2450  # 1225 + 1225
        assert result["total_thoughts"] == 100
        assert result["batch_size"] == 50
        assert "duration_seconds" in result

        # RPC 호출 횟수 검증 (2 batches)
        assert mock_supabase_service.client.rpc.call_count == 2

    @pytest.mark.asyncio
    async def test_build_distance_table_batched_partial_failure(
        self, distance_service, mock_supabase_service
    ):
        """일부 배치 실패 케이스"""
        # Mock count response
        count_response = MagicMock()
        count_response.count = 150
        mock_supabase_service.client.table.return_value.select.return_value.not_.is_.return_value.execute = AsyncMock(
            return_value=count_response
        )

        # Mock truncate
        mock_supabase_service.client.table.return_value.delete.return_value.neq.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[])
        )

        # Mock RPC calls (3 batches, 2번째 실패)
        # Batch 1 성공
        rpc1 = MagicMock()
        rpc1.execute = AsyncMock(return_value=MagicMock(data={"pairs_inserted": 1225}))

        # Batch 2 실패 - rpc() 호출 자체는 성공하지만 execute()에서 실패
        rpc2 = MagicMock()
        rpc2.execute = AsyncMock(side_effect=Exception("RPC timeout"))

        # Batch 3 성공
        rpc3 = MagicMock()
        rpc3.execute = AsyncMock(return_value=MagicMock(data={"pairs_inserted": 1225}))

        mock_supabase_service.client.rpc = MagicMock(side_effect=[rpc1, rpc2, rpc3])

        # Execute
        result = await distance_service.build_distance_table_batched(batch_size=50)

        # Assertions: 실패한 배치 제외하고 성공한 것만 카운트
        assert result["success"] is True
        assert result["total_pairs"] == 2450  # 1225 + 0 + 1225
        assert result["total_thoughts"] == 150
        assert mock_supabase_service.client.rpc.call_count == 3

    @pytest.mark.asyncio
    async def test_build_distance_table_batched_empty_thoughts(
        self, distance_service, mock_supabase_service
    ):
        """thought_units가 없는 경우"""
        # Mock count response (0 thoughts)
        count_response = MagicMock()
        count_response.count = 0
        mock_supabase_service.client.table.return_value.select.return_value.not_.is_.return_value.execute = AsyncMock(
            return_value=count_response
        )

        # Execute
        result = await distance_service.build_distance_table_batched(batch_size=50)

        # Assertions
        assert result["success"] is True
        assert result["total_pairs"] == 0
        assert result["total_thoughts"] == 0
        assert result["duration_seconds"] == 0

        # RPC는 호출되지 않아야 함
        mock_supabase_service.client.rpc.assert_not_called()

    @pytest.mark.asyncio
    async def test_build_distance_table_batched_no_data_from_rpc(
        self, distance_service, mock_supabase_service
    ):
        """RPC가 data 없이 반환하는 경우"""
        # Mock count response
        count_response = MagicMock()
        count_response.count = 50
        mock_supabase_service.client.table.return_value.select.return_value.not_.is_.return_value.execute = AsyncMock(
            return_value=count_response
        )

        # Mock truncate
        mock_supabase_service.client.table.return_value.delete.return_value.neq.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[])
        )

        # Mock RPC: data가 None인 응답
        rpc_mock = MagicMock()
        rpc_mock.execute = AsyncMock(return_value=MagicMock(data=None))
        mock_supabase_service.client.rpc = MagicMock(return_value=rpc_mock)

        # Execute
        result = await distance_service.build_distance_table_batched(batch_size=50)

        # Assertions: pairs_inserted가 없으면 0으로 처리
        assert result["success"] is True
        assert result["total_pairs"] == 0


class TestUpdateDistanceTableIncremental:
    """update_distance_table_incremental() 테스트"""

    @pytest.mark.asyncio
    async def test_update_distance_table_incremental_auto_detect(
        self, distance_service, mock_supabase_service
    ):
        """자동 감지 모드 (new_thought_ids=None)"""
        # Mock RPC response
        rpc_mock = MagicMock()
        rpc_mock.execute = AsyncMock(
            return_value=MagicMock(
                data={
                    "new_thought_count": 10,
                    "new_pairs_inserted": 19210
                }
            )
        )
        mock_supabase_service.client.rpc = MagicMock(return_value=rpc_mock)

        # Execute (auto-detect)
        result = await distance_service.update_distance_table_incremental(
            new_thought_ids=None
        )

        # Assertions
        assert result["success"] is True
        assert result["new_thought_count"] == 10
        assert result["new_pairs_inserted"] == 19210

        # RPC 호출 검증
        mock_supabase_service.client.rpc.assert_called_once_with(
            "update_distance_table_incremental",
            {"new_thought_ids": None}
        )

    @pytest.mark.asyncio
    async def test_update_distance_table_incremental_manual(
        self, distance_service, mock_supabase_service
    ):
        """수동 지정 모드 (new_thought_ids=[1,2,3])"""
        # Mock RPC response
        rpc_mock = MagicMock()
        rpc_mock.execute = AsyncMock(
            return_value=MagicMock(
                data={
                    "new_thought_count": 3,
                    "new_pairs_inserted": 5763
                }
            )
        )
        mock_supabase_service.client.rpc = MagicMock(return_value=rpc_mock)

        # Execute (manual)
        result = await distance_service.update_distance_table_incremental(
            new_thought_ids=[1, 2, 3]
        )

        # Assertions
        assert result["success"] is True
        assert result["new_thought_count"] == 3
        assert result["new_pairs_inserted"] == 5763

        # RPC 호출 검증
        mock_supabase_service.client.rpc.assert_called_once_with(
            "update_distance_table_incremental",
            {"new_thought_ids": [1, 2, 3]}
        )

    @pytest.mark.asyncio
    async def test_update_distance_table_incremental_no_new_thoughts(
        self, distance_service, mock_supabase_service
    ):
        """신규 thought가 없는 경우"""
        # Mock RPC response (0 new thoughts)
        rpc_mock = MagicMock()
        rpc_mock.execute = AsyncMock(
            return_value=MagicMock(
                data={
                    "new_thought_count": 0,
                    "new_pairs_inserted": 0
                }
            )
        )
        mock_supabase_service.client.rpc = MagicMock(return_value=rpc_mock)

        # Execute
        result = await distance_service.update_distance_table_incremental()

        # Assertions
        assert result["success"] is True
        assert result["new_thought_count"] == 0
        assert result["new_pairs_inserted"] == 0

    @pytest.mark.asyncio
    async def test_update_distance_table_incremental_rpc_failure(
        self, distance_service, mock_supabase_service
    ):
        """RPC 호출 실패"""
        # Mock RPC failure (execute()에서 실패)
        rpc_mock = MagicMock()
        rpc_mock.execute = AsyncMock(
            side_effect=Exception("RPC function does not exist")
        )
        mock_supabase_service.client.rpc = MagicMock(return_value=rpc_mock)

        # Execute and expect exception
        with pytest.raises(Exception, match="RPC function does not exist"):
            await distance_service.update_distance_table_incremental()

    @pytest.mark.asyncio
    async def test_update_distance_table_incremental_no_data(
        self, distance_service, mock_supabase_service
    ):
        """RPC가 data 없이 반환하는 경우"""
        # Mock RPC response (no data)
        rpc_mock = MagicMock()
        rpc_mock.execute = AsyncMock(return_value=MagicMock(data=None))
        mock_supabase_service.client.rpc = MagicMock(return_value=rpc_mock)

        # Execute and expect exception
        with pytest.raises(Exception, match="RPC returned no data"):
            await distance_service.update_distance_table_incremental()


class TestGetStatistics:
    """get_statistics() 테스트"""

    @pytest.mark.asyncio
    async def test_get_statistics_success(
        self, distance_service, mock_supabase_service
    ):
        """통계 조회 성공"""
        # Mock count response
        count_response = MagicMock()
        count_response.count = 1846210
        mock_supabase_service.client.table.return_value.select.return_value.execute = AsyncMock(
            return_value=count_response
        )

        # Mock sample response (similarity 값들)
        sample_response = MagicMock(
            data=[
                {"similarity": 0.001},
                {"similarity": 0.423},
                {"similarity": 0.999},
            ]
        )
        mock_supabase_service.client.table.return_value.select.return_value.limit.return_value.execute = AsyncMock(
            return_value=sample_response
        )

        # Execute
        result = await distance_service.get_statistics()

        # Assertions
        assert result["total_pairs"] == 1846210
        assert result["min_similarity"] == 0.001
        assert result["max_similarity"] == 0.999
        assert result["avg_similarity"] == pytest.approx((0.001 + 0.423 + 0.999) / 3)

    @pytest.mark.asyncio
    async def test_get_statistics_empty_table(
        self, distance_service, mock_supabase_service
    ):
        """빈 테이블 (total_pairs=0)"""
        # Mock count response (0 pairs)
        count_response = MagicMock()
        count_response.count = 0
        mock_supabase_service.client.table.return_value.select.return_value.execute = AsyncMock(
            return_value=count_response
        )

        # Execute
        result = await distance_service.get_statistics()

        # Assertions
        assert result["total_pairs"] == 0
        assert result["min_similarity"] is None
        assert result["max_similarity"] is None
        assert result["avg_similarity"] is None

    @pytest.mark.asyncio
    async def test_get_statistics_empty_sample(
        self, distance_service, mock_supabase_service
    ):
        """count는 있지만 sample이 빈 경우"""
        # Mock count response
        count_response = MagicMock()
        count_response.count = 100
        mock_supabase_service.client.table.return_value.select.return_value.execute = AsyncMock(
            return_value=count_response
        )

        # Mock sample response (empty data)
        sample_response = MagicMock(data=[])
        mock_supabase_service.client.table.return_value.select.return_value.limit.return_value.execute = AsyncMock(
            return_value=sample_response
        )

        # Execute
        result = await distance_service.get_statistics()

        # Assertions
        assert result["total_pairs"] == 100
        assert result["min_similarity"] is None
        assert result["max_similarity"] is None
        assert result["avg_similarity"] is None

    @pytest.mark.asyncio
    async def test_get_statistics_count_none(
        self, distance_service, mock_supabase_service
    ):
        """count가 None인 경우"""
        # Mock count response (count is None)
        count_response = MagicMock()
        count_response.count = None
        mock_supabase_service.client.table.return_value.select.return_value.execute = AsyncMock(
            return_value=count_response
        )

        # Execute
        result = await distance_service.get_statistics()

        # Assertions
        assert result["total_pairs"] == 0
        assert result["min_similarity"] is None
        assert result["max_similarity"] is None
        assert result["avg_similarity"] is None
