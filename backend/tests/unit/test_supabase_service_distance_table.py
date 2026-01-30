"""
Supabase Service - Distance Table 조회 유닛 테스트.

테스트 범위:
- get_candidates_from_distance_table: Distance Table에서 후보 조회
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.supabase_service import SupabaseService


@pytest.fixture
def supabase_service():
    """SupabaseService fixture with mocked client."""
    service = SupabaseService()
    service._initialized = True
    service.client = MagicMock()
    return service


class TestGetCandidatesFromDistanceTable:
    """get_candidates_from_distance_table() 테스트"""

    @pytest.mark.asyncio
    async def test_get_candidates_from_distance_table_success(self, supabase_service):
        """정상 조회 (2단계 쿼리)"""
        # Mock Step 1: thought_pair_distances 조회
        pairs_response = MagicMock(
            data=[
                {"thought_a_id": 1, "thought_b_id": 2, "similarity": 0.15},
                {"thought_a_id": 3, "thought_b_id": 4, "similarity": 0.25},
            ]
        )

        # Mock Step 2: thought_units 조회
        thoughts_response = MagicMock(
            data=[
                {"id": 1, "claim": "사고 A", "raw_note_id": "note-a"},
                {"id": 2, "claim": "사고 B", "raw_note_id": "note-b"},
                {"id": 3, "claim": "사고 C", "raw_note_id": "note-c"},
                {"id": 4, "claim": "사고 D", "raw_note_id": "note-d"},
            ]
        )

        # Setup mock chain
        table_mock = MagicMock()

        # Step 1 chain (with .limit() added)
        select_mock_1 = MagicMock()
        mock_execute_1 = AsyncMock(return_value=pairs_response)
        mock_limit_1 = MagicMock(execute=mock_execute_1)
        select_mock_1.gte.return_value.lte.return_value.order.return_value.limit.return_value = mock_limit_1

        # Step 2 chain
        select_mock_2 = MagicMock()
        select_mock_2.in_.return_value.execute = AsyncMock(
            return_value=thoughts_response
        )

        # table() 호출 순서에 따라 다른 mock 반환
        call_count = [0]
        def table_side_effect(table_name):
            if table_name == "thought_pair_distances":
                return_mock = MagicMock()
                return_mock.select.return_value = select_mock_1
                return return_mock
            elif table_name == "thought_units":
                return_mock = MagicMock()
                return_mock.select.return_value = select_mock_2
                return return_mock

        supabase_service.client.table = MagicMock(side_effect=table_side_effect)

        # Execute
        result = await supabase_service.get_candidates_from_distance_table(
            min_similarity=0.05,
            max_similarity=0.35
        )

        # Assertions
        assert len(result) == 2

        # First pair
        assert result[0]["thought_a_id"] == 1
        assert result[0]["thought_b_id"] == 2
        assert result[0]["thought_a_claim"] == "사고 A"
        assert result[0]["thought_b_claim"] == "사고 B"
        assert result[0]["similarity"] == 0.15
        assert result[0]["raw_note_id_a"] == "note-a"
        assert result[0]["raw_note_id_b"] == "note-b"

        # Second pair
        assert result[1]["thought_a_id"] == 3
        assert result[1]["thought_b_id"] == 4
        assert result[1]["thought_a_claim"] == "사고 C"
        assert result[1]["thought_b_claim"] == "사고 D"

    @pytest.mark.asyncio
    async def test_get_candidates_from_distance_table_empty(self, supabase_service):
        """빈 결과 (유사도 범위 내 페어 없음)"""
        # Mock empty pairs response
        pairs_response = MagicMock(data=[])

        select_mock = MagicMock()
        mock_execute = AsyncMock(return_value=pairs_response)
        mock_limit = MagicMock(execute=mock_execute)
        select_mock.gte.return_value.lte.return_value.order.return_value.limit.return_value = mock_limit

        table_mock = MagicMock()
        table_mock.select.return_value = select_mock
        supabase_service.client.table.return_value = table_mock

        # Execute
        result = await supabase_service.get_candidates_from_distance_table(
            min_similarity=0.05,
            max_similarity=0.35
        )

        # Assertions
        assert result == []

    @pytest.mark.asyncio
    async def test_get_candidates_from_distance_table_missing_thought(
        self, supabase_service
    ):
        """thought_units에 없는 ID가 있는 경우 (데이터 정합성 문제)"""
        # Mock Step 1: pairs (thought_id=5는 존재하지 않음)
        pairs_response = MagicMock(
            data=[
                {"thought_a_id": 1, "thought_b_id": 5, "similarity": 0.15},
            ]
        )

        # Mock Step 2: thoughts (id=5는 없음)
        thoughts_response = MagicMock(
            data=[
                {"id": 1, "claim": "사고 A", "raw_note_id": "note-a"},
            ]
        )

        # Setup mock chain (with .limit() added)
        select_mock_1 = MagicMock()
        mock_execute_1 = AsyncMock(return_value=pairs_response)
        mock_limit_1 = MagicMock(execute=mock_execute_1)
        select_mock_1.gte.return_value.lte.return_value.order.return_value.limit.return_value = mock_limit_1

        select_mock_2 = MagicMock()
        select_mock_2.in_.return_value.execute = AsyncMock(
            return_value=thoughts_response
        )

        def table_side_effect(table_name):
            if table_name == "thought_pair_distances":
                return_mock = MagicMock()
                return_mock.select.return_value = select_mock_1
                return return_mock
            elif table_name == "thought_units":
                return_mock = MagicMock()
                return_mock.select.return_value = select_mock_2
                return return_mock

        supabase_service.client.table = MagicMock(side_effect=table_side_effect)

        # Execute
        result = await supabase_service.get_candidates_from_distance_table(
            min_similarity=0.05,
            max_similarity=0.35
        )

        # Assertions: missing thought는 스킵됨
        assert result == []

    @pytest.mark.asyncio
    async def test_get_candidates_from_distance_table_large_limit(
        self, supabase_service
    ):
        """대량 조회 (limit=50000)"""
        # Mock large dataset (100개)
        pairs_data = [
            {"thought_a_id": i*2, "thought_b_id": i*2+1, "similarity": 0.1 + i*0.001}
            for i in range(100)
        ]
        pairs_response = MagicMock(data=pairs_data)

        # Mock thoughts (200개)
        thoughts_data = [
            {"id": i, "claim": f"사고 {i}", "raw_note_id": f"note-{i}"}
            for i in range(200)
        ]
        thoughts_response = MagicMock(data=thoughts_data)

        # Setup mock chain (with .limit() added)
        select_mock_1 = MagicMock()
        mock_execute_1 = AsyncMock(return_value=pairs_response)
        mock_limit_1 = MagicMock(execute=mock_execute_1)
        select_mock_1.gte.return_value.lte.return_value.order.return_value.limit.return_value = mock_limit_1

        select_mock_2 = MagicMock()
        select_mock_2.in_.return_value.execute = AsyncMock(
            return_value=thoughts_response
        )

        def table_side_effect(table_name):
            if table_name == "thought_pair_distances":
                return_mock = MagicMock()
                return_mock.select.return_value = select_mock_1
                return return_mock
            elif table_name == "thought_units":
                return_mock = MagicMock()
                return_mock.select.return_value = select_mock_2
                return return_mock

        supabase_service.client.table = MagicMock(side_effect=table_side_effect)

        # Execute
        result = await supabase_service.get_candidates_from_distance_table(
            min_similarity=0.05,
            max_similarity=0.35
        )

        # Assertions
        assert len(result) == 100

    @pytest.mark.asyncio
    async def test_get_candidates_from_distance_table_ordering(
        self, supabase_service
    ):
        """낮은 유사도부터 정렬 확인"""
        # Mock pairs (similarity DESC는 DB에서 처리되므로 여기서는 검증만)
        pairs_response = MagicMock(
            data=[
                {"thought_a_id": 1, "thought_b_id": 2, "similarity": 0.05},
                {"thought_a_id": 3, "thought_b_id": 4, "similarity": 0.35},
            ]
        )

        thoughts_response = MagicMock(
            data=[
                {"id": 1, "claim": "사고 A", "raw_note_id": "note-a"},
                {"id": 2, "claim": "사고 B", "raw_note_id": "note-b"},
                {"id": 3, "claim": "사고 C", "raw_note_id": "note-c"},
                {"id": 4, "claim": "사고 D", "raw_note_id": "note-d"},
            ]
        )

        # Setup mock chain (with .limit() added)
        select_mock_1 = MagicMock()
        mock_execute = AsyncMock(return_value=pairs_response)
        mock_limit = MagicMock(execute=mock_execute)
        order_mock = MagicMock()
        order_mock.limit.return_value = mock_limit
        select_mock_1.gte.return_value.lte.return_value.order.return_value = order_mock

        select_mock_2 = MagicMock()
        select_mock_2.in_.return_value.execute = AsyncMock(
            return_value=thoughts_response
        )

        def table_side_effect(table_name):
            if table_name == "thought_pair_distances":
                return_mock = MagicMock()
                return_mock.select.return_value = select_mock_1
                return return_mock
            elif table_name == "thought_units":
                return_mock = MagicMock()
                return_mock.select.return_value = select_mock_2
                return return_mock

        supabase_service.client.table = MagicMock(side_effect=table_side_effect)

        # Execute
        result = await supabase_service.get_candidates_from_distance_table(
            min_similarity=0.05,
            max_similarity=0.35
        )

        # Assertions: order 호출 검증
        select_mock_1.gte.return_value.lte.return_value.order.assert_called_once_with(
            "similarity", desc=False
        )

        # 결과 순서 검증 (낮은 유사도부터)
        assert result[0]["similarity"] == 0.05
        assert result[1]["similarity"] == 0.35


class TestRangeValidation:
    """80% 범위 검증 로직 테스트"""

    @pytest.mark.asyncio
    async def test_range_validation_pass_30_percent(self, supabase_service):
        """30% 범위 (정상, p10_p40)"""
        # Mock 응답 설정
        pairs_response = MagicMock(data=[])
        select_mock = MagicMock()
        mock_execute = AsyncMock(return_value=pairs_response)
        mock_limit = MagicMock(execute=mock_execute)
        select_mock.gte.return_value.lte.return_value.order.return_value.limit.return_value = mock_limit

        table_mock = MagicMock()
        table_mock.select.return_value = select_mock
        supabase_service.client.table.return_value = table_mock

        # Execute - ValueError가 발생하지 않아야 함
        result = await supabase_service.get_candidates_from_distance_table(
            min_similarity=0.057,
            max_similarity=0.093
        )

        # Assertions
        assert result == []  # 빈 결과이지만 예외 발생 없음

    @pytest.mark.asyncio
    async def test_range_validation_pass_boundary(self, supabase_service):
        """80% 경계값 (통과) - 정확히 80%"""
        # Mock 응답 설정
        pairs_response = MagicMock(data=[])
        select_mock = MagicMock()
        mock_execute = AsyncMock(return_value=pairs_response)
        mock_limit = MagicMock(execute=mock_execute)
        select_mock.gte.return_value.lte.return_value.order.return_value.limit.return_value = mock_limit

        table_mock = MagicMock()
        table_mock.select.return_value = select_mock
        supabase_service.client.table.return_value = table_mock

        # Execute - 정확히 80%는 통과해야 함 (> 0.8 조건)
        result = await supabase_service.get_candidates_from_distance_table(
            min_similarity=0.0,
            max_similarity=0.8
        )

        # Assertions
        assert result == []  # 빈 결과이지만 예외 발생 없음

    @pytest.mark.asyncio
    async def test_range_validation_fail_81_percent(self, supabase_service):
        """81% 범위 (차단) - 80% 초과"""
        # Execute - ValueError 발생 예상
        with pytest.raises(ValueError) as exc_info:
            await supabase_service.get_candidates_from_distance_table(
                min_similarity=0.0,
                max_similarity=0.81
            )

        # Assertions: 에러 메시지 검증
        error_msg = str(exc_info.value)
        assert "too wide" in error_msg.lower()
        assert "80%" in error_msg or "0.8" in error_msg
        assert "p10_p40" in error_msg or "p30_p60" in error_msg

    @pytest.mark.asyncio
    async def test_range_validation_fail_100_percent(self, supabase_service):
        """100% 범위 (차단, p0_p100)"""
        # Execute - ValueError 발생 예상
        with pytest.raises(ValueError) as exc_info:
            await supabase_service.get_candidates_from_distance_table(
                min_similarity=0.0,
                max_similarity=1.0
            )

        # Assertions: 에러 메시지 검증
        error_msg = str(exc_info.value)
        assert "too wide" in error_msg.lower()
        assert "100%" in error_msg or "1.0" in error_msg

    @pytest.mark.asyncio
    async def test_no_limit_parameter(self, supabase_service):
        """limit 파라미터가 제거되었음을 확인"""
        # 1. 메서드 시그니처 검증
        import inspect
        sig = inspect.signature(supabase_service.get_candidates_from_distance_table)
        params = list(sig.parameters.keys())

        # limit 파라미터가 없어야 함
        assert "limit" not in params
        # self는 inspect.signature에서 제외되므로 확인하지 않음
        assert "min_similarity" in params
        assert "max_similarity" in params
        # 파라미터는 정확히 2개여야 함 (min_similarity, max_similarity)
        assert len(params) == 2

        # 2. limit 전달 시 TypeError 발생 확인
        pairs_response = MagicMock(data=[])
        select_mock = MagicMock()
        select_mock.gte.return_value.lte.return_value.order.return_value.execute = AsyncMock(
            return_value=pairs_response
        )

        table_mock = MagicMock()
        table_mock.select.return_value = select_mock
        supabase_service.client.table.return_value = table_mock

        # Execute - TypeError 발생 예상
        with pytest.raises(TypeError) as exc_info:
            await supabase_service.get_candidates_from_distance_table(
                min_similarity=0.05,
                max_similarity=0.35,
                limit=50000  # 더 이상 지원되지 않는 파라미터
            )

        # Assertions
        error_msg = str(exc_info.value)
        assert "limit" in error_msg or "unexpected keyword argument" in error_msg
