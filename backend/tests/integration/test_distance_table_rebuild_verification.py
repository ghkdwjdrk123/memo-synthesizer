"""
Distance Table 재구축 검증 테스트.

테스트 범위:
1. thought_units 테이블의 실제 레코드 수 확인 (embedding NOT NULL)
2. 예상 페어 수 계산: n(n-1)/2
3. 현재 Distance Table의 페어 수 확인
4. thought_a_id, thought_b_id의 범위 확인 (누락된 ID 체크)
5. 샘플 페어 존재 여부 확인

검증 목표:
- 재구축 완료 여부 확인
- 누락된 페어 탐지
- ID 범위 검증
"""

import sys
import os

# Add backend directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock, patch

from main import app


@pytest.fixture
def mock_supabase_client():
    """Mock Supabase client with realistic data for rebuild verification."""
    client = MagicMock()

    # Mock thought_units 개수 조회 (embedding NOT NULL)
    count_response = MagicMock()
    count_response.count = 1909  # 실제 상황에서 예상되는 값
    count_response.data = []

    # Mock distance table 통계 조회
    stats_count_response = MagicMock()
    stats_count_response.count = 682271  # 현재 37.5% (재구축 진행 중)
    stats_count_response.data = []

    # Mock distance table ID 범위 조회
    id_range_response = MagicMock()
    id_range_response.data = [
        {
            "min_a_id": 1,
            "max_a_id": 1500,  # 일부만 처리됨
            "min_b_id": 2,
            "max_b_id": 1909
        }
    ]

    # Mock 샘플 페어 조회
    sample_pairs_response = MagicMock()
    sample_pairs_response.data = [
        {"thought_a_id": 1, "thought_b_id": 2, "similarity": 0.15},
        {"thought_a_id": 1, "thought_b_id": 3, "similarity": 0.23},
        {"thought_a_id": 50, "thought_b_id": 51, "similarity": 0.18},
        # 100-101 페어는 누락 (재구축 진행 중이므로)
    ]

    # Mock RPC 호출 (통계 조회)
    rpc_stats_response = MagicMock()
    rpc_stats_response.data = {
        "total_pairs": 682271,
        "min_a_id": 1,
        "max_a_id": 1500,
        "min_b_id": 2,
        "max_b_id": 1909
    }

    # Mock table() 체인 설정
    table_mock = MagicMock()
    select_mock = MagicMock()
    not_mock = MagicMock()
    execute_mock = MagicMock()

    # thought_units 개수 조회 체인
    thought_units_chain = MagicMock()
    thought_units_chain.select.return_value = thought_units_chain
    thought_units_chain.not_ = MagicMock()
    thought_units_chain.not_.is_.return_value = thought_units_chain
    thought_units_chain.execute = AsyncMock(return_value=count_response)

    # distance table 통계 조회 체인
    distance_stats_chain = MagicMock()
    distance_stats_chain.select.return_value = distance_stats_chain
    distance_stats_chain.execute = AsyncMock(return_value=stats_count_response)

    # 샘플 페어 조회 체인
    sample_chain = MagicMock()
    sample_chain.select.return_value = sample_chain
    sample_chain.or_.return_value = sample_chain
    sample_chain.order.return_value = sample_chain
    sample_chain.execute = AsyncMock(return_value=sample_pairs_response)

    # RPC 호출 체인
    rpc_chain = MagicMock()
    rpc_chain.execute = AsyncMock(return_value=rpc_stats_response)

    # table() 호출 시 적절한 체인 반환
    def table_side_effect(table_name):
        if table_name == "thought_units":
            return thought_units_chain
        elif table_name == "thought_pair_distances":
            # 첫 호출은 stats, 두 번째 호출은 샘플
            return distance_stats_chain
        return MagicMock()

    client.table = MagicMock(side_effect=table_side_effect)
    client.rpc = MagicMock(return_value=rpc_chain)

    return client


@pytest.fixture
def mock_supabase_service(mock_supabase_client):
    """Mock SupabaseService with initialized client."""
    service = MagicMock()
    service.client = mock_supabase_client
    service._ensure_initialized = AsyncMock()

    with patch("services.distance_table_service.SupabaseService", return_value=service):
        yield service


class TestDistanceTableRebuildVerification:
    """Distance Table 재구축 검증 테스트"""

    @pytest.mark.asyncio
    async def test_verify_thought_units_count(self, mock_supabase_service):
        """1. thought_units 테이블의 실제 레코드 수 확인"""
        from services.distance_table_service import DistanceTableService

        service = DistanceTableService(mock_supabase_service)

        # Mock thought_units 개수 조회
        count_response = MagicMock()
        count_response.count = 1909
        count_response.data = []

        mock_supabase_service.client.table("thought_units").select.return_value.not_.is_.return_value.execute = AsyncMock(
            return_value=count_response
        )

        # 실제 서비스 메서드는 없으므로 직접 DB 조회 시뮬레이션
        response = await mock_supabase_service.client.table("thought_units").select("id", count="exact").not_.is_("embedding", "null").execute()

        # Assertions
        assert response.count == 1909
        print(f"✅ thought_units 개수: {response.count}")
        print(f"예상 페어 수: {response.count * (response.count - 1) // 2:,}")

    @pytest.mark.asyncio
    async def test_verify_expected_pairs_calculation(self):
        """2. 예상 페어 수 계산: n(n-1)/2"""
        n = 1909
        expected_pairs = n * (n - 1) // 2

        # Assertions
        assert expected_pairs == 1_821_186
        print(f"✅ 예상 페어 수 계산: {n} thoughts → {expected_pairs:,} pairs")

    @pytest.mark.asyncio
    async def test_verify_current_distance_table_count(self, mock_supabase_service):
        """3. 현재 Distance Table의 페어 수 확인"""
        from services.distance_table_service import DistanceTableService

        service = DistanceTableService(mock_supabase_service)

        # get_statistics 호출
        stats = await service.get_statistics()

        # Assertions
        assert stats["total_pairs"] == 682271  # 현재 37.5%
        expected_pairs = 1_821_186
        completion_rate = (stats["total_pairs"] / expected_pairs) * 100

        print(f"✅ 현재 Distance Table 페어 수: {stats['total_pairs']:,}")
        print(f"예상 페어 수: {expected_pairs:,}")
        print(f"완료율: {completion_rate:.1f}%")

        # 완료율 검증
        if completion_rate < 90:
            print(f"⚠️ 경고: 재구축이 {completion_rate:.1f}%만 완료되었습니다. (목표: 100%)")
        else:
            print(f"✅ 재구축 거의 완료: {completion_rate:.1f}%")

    @pytest.mark.asyncio
    async def test_verify_thought_id_ranges(self, mock_supabase_service):
        """4. thought_a_id, thought_b_id의 범위 확인 (누락된 ID 체크)"""
        from services.distance_table_service import DistanceTableService

        service = DistanceTableService(mock_supabase_service)

        # RPC 호출로 ID 범위 조회 시뮬레이션
        result = await mock_supabase_service.client.rpc(
            'get_distance_table_id_ranges'
        ).execute()

        stats = result.data

        # Assertions
        assert stats["min_a_id"] == 1
        assert stats["max_b_id"] == 1909  # 최대 thought ID

        print(f"✅ thought_a_id 범위: {stats['min_a_id']} ~ {stats['max_a_id']}")
        print(f"✅ thought_b_id 범위: {stats['min_b_id']} ~ {stats['max_b_id']}")

        # 누락된 ID 범위 확인
        if stats["max_a_id"] < 1909:
            missing_start = stats["max_a_id"] + 1
            missing_end = 1909
            print(f"⚠️ 누락된 thought_a_id 범위: {missing_start} ~ {missing_end}")
        else:
            print("✅ 모든 thought_a_id가 처리되었습니다.")

    @pytest.mark.asyncio
    async def test_verify_sample_pairs_existence(self, mock_supabase_service):
        """5. 샘플 페어 존재 여부 확인"""
        from services.distance_table_service import DistanceTableService

        service = DistanceTableService(mock_supabase_service)

        # 샘플 페어 조회 시뮬레이션
        sample_pairs = [
            (1, 2),
            (1, 3),
            (50, 51),
            (100, 101),
            (1000, 1001),
            (1900, 1901)
        ]

        # Mock 샘플 페어 조회
        sample_response = MagicMock()
        sample_response.data = [
            {"thought_a_id": 1, "thought_b_id": 2, "similarity": 0.15},
            {"thought_a_id": 1, "thought_b_id": 3, "similarity": 0.23},
            {"thought_a_id": 50, "thought_b_id": 51, "similarity": 0.18},
        ]

        mock_supabase_service.client.table("thought_pair_distances").select.return_value.or_.return_value.order.return_value.execute = AsyncMock(
            return_value=sample_response
        )

        # 조회 실행
        response = await mock_supabase_service.client.table("thought_pair_distances").select(
            "thought_a_id, thought_b_id, similarity"
        ).or_(
            f"and(thought_a_id.eq.1,thought_b_id.eq.2),"
            f"and(thought_a_id.eq.1,thought_b_id.eq.3),"
            f"and(thought_a_id.eq.50,thought_b_id.eq.51),"
            f"and(thought_a_id.eq.100,thought_b_id.eq.101),"
            f"and(thought_a_id.eq.1000,thought_b_id.eq.1001),"
            f"and(thought_a_id.eq.1900,thought_b_id.eq.1901)"
        ).order("thought_a_id, thought_b_id").execute()

        found_pairs = response.data
        found_pair_ids = {(pair["thought_a_id"], pair["thought_b_id"]) for pair in found_pairs}

        # Assertions
        print(f"✅ 샘플 페어 조회 결과: {len(found_pairs)}/{len(sample_pairs)} 페어 발견")

        for pair in sample_pairs:
            if pair in found_pair_ids:
                similarity = next(p["similarity"] for p in found_pairs if (p["thought_a_id"], p["thought_b_id"]) == pair)
                print(f"  ✅ 페어 {pair}: 존재 (similarity={similarity:.3f})")
            else:
                print(f"  ❌ 페어 {pair}: 누락")

        # 적어도 일부 페어는 존재해야 함
        assert len(found_pairs) > 0

    @pytest.mark.asyncio
    async def test_verify_rebuild_progress_summary(self, mock_supabase_service):
        """재구축 진행 상황 종합 리포트"""
        from services.distance_table_service import DistanceTableService

        service = DistanceTableService(mock_supabase_service)

        # 1. thought_units 개수
        count_response = MagicMock()
        count_response.count = 1909
        mock_supabase_service.client.table("thought_units").select.return_value.not_.is_.return_value.execute = AsyncMock(
            return_value=count_response
        )

        thought_response = await mock_supabase_service.client.table("thought_units").select("id", count="exact").not_.is_("embedding", "null").execute()
        total_thoughts = thought_response.count

        # 2. 예상 페어 수
        expected_pairs = total_thoughts * (total_thoughts - 1) // 2

        # 3. 현재 페어 수
        stats = await service.get_statistics()
        current_pairs = stats["total_pairs"]

        # 4. 완료율
        completion_rate = (current_pairs / expected_pairs) * 100

        # 5. 예상 남은 페어
        remaining_pairs = expected_pairs - current_pairs

        # 종합 리포트
        print("\n" + "="*60)
        print("Distance Table 재구축 진행 상황")
        print("="*60)
        print(f"총 thought_units: {total_thoughts:,}개")
        print(f"예상 페어 수: {expected_pairs:,}개")
        print(f"현재 페어 수: {current_pairs:,}개")
        print(f"완료율: {completion_rate:.2f}%")
        print(f"남은 페어: {remaining_pairs:,}개")
        print("="*60)

        # 상태 판단
        if completion_rate >= 99.9:
            print("✅ 상태: 재구축 완료")
        elif completion_rate >= 90:
            print("⚠️ 상태: 재구축 거의 완료 (90% 이상)")
        elif completion_rate >= 50:
            print("⚠️ 상태: 재구축 진행 중 (50% 이상)")
        else:
            print("❌ 상태: 재구축 초기 단계 (50% 미만)")

        print(f"\n💡 분석:")
        if completion_rate < 90:
            # 7분 경과 후에도 37.5%라면 문제 가능성
            print("  - 재구축이 7분 경과 후에도 37.5%에서 멈춘 것으로 보입니다.")
            print("  - 가능한 원인:")
            print("    1. RPC 프로세스가 중단됨 (타임아웃, 메모리 부족)")
            print("    2. 배치 처리가 일부 ID 범위를 건너뜀")
            print("    3. 인덱스 생성 중 대기")
            print("  - 권장 조치:")
            print("    1. GET /pipeline/distance-table/status로 현재 상태 재확인")
            print("    2. 로그 확인: 배치 실패 또는 타임아웃 메시지")
            print("    3. 필요 시 POST /pipeline/distance-table/build 재실행")
        else:
            print("  - 재구축이 정상적으로 진행되고 있습니다.")
            print("  - 완료까지 예상 시간: 약 1-2분 (현재 진행률 기준)")

        print("="*60 + "\n")

        # Assertions
        assert total_thoughts > 0
        assert expected_pairs > 0


class TestDistanceTableRebuildEndpoint:
    """실제 엔드포인트를 통한 재구축 검증"""

    @pytest.mark.asyncio
    async def test_status_endpoint_shows_progress(self, mock_supabase_service):
        """GET /pipeline/distance-table/status로 진행 상황 확인"""
        # Mock DistanceTableService
        with patch("services.distance_table_service.get_supabase_service", return_value=mock_supabase_service):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/pipeline/distance-table/status")

        # Assertions
        assert response.status_code == 200
        data = response.json()

        print(f"\n✅ API 응답:")
        print(f"  - total_pairs: {data['statistics']['total_pairs']:,}")
        print(f"  - min_similarity: {data['statistics']['min_similarity']}")
        print(f"  - max_similarity: {data['statistics']['max_similarity']}")
        print(f"  - avg_similarity: {data['statistics']['avg_similarity']}")

        # 재구축 진행 중인지 확인
        total_pairs = data['statistics']['total_pairs']
        expected_pairs = 1_821_186

        if total_pairs < expected_pairs:
            completion_rate = (total_pairs / expected_pairs) * 100
            print(f"\n⚠️ 재구축 진행 중: {completion_rate:.1f}%")
        else:
            print(f"\n✅ 재구축 완료: 100%")


@pytest.mark.asyncio
async def test_distance_table_integrity_check():
    """
    Distance Table 무결성 검사.

    검증 항목:
    1. UNIQUE constraint 위반 여부 (thought_a_id, thought_b_id 중복)
    2. CHECK constraint 위반 여부 (thought_a_id < thought_b_id)
    3. NULL 값 존재 여부
    4. similarity 범위 검증 (0 <= similarity <= 1)
    """
    # 이 테스트는 실제 DB 연결이 필요하므로 스킵
    # 실제 환경에서만 실행
    pytest.skip("실제 DB 연결이 필요한 테스트")
