"""
Distance Table 재구축 실시간 검증 (실제 DB 연결).

⚠️ 주의: 이 테스트는 실제 Supabase DB에 연결합니다.
환경변수 SUPABASE_URL, SUPABASE_KEY가 필요합니다.

실행 방법:
    pytest tests/integration/test_distance_table_rebuild_live.py -v -s

테스트 범위:
1. thought_units 테이블의 실제 레코드 수 확인
2. Distance Table 현재 페어 수 확인
3. ID 범위 검증
4. 샘플 페어 조회
5. 재구축 진행 상황 리포트
"""

import sys
import os

# Add backend directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest
import asyncio
from typing import Dict, Any, List, Tuple

from services.supabase_service import SupabaseService, get_supabase_service
from services.distance_table_service import DistanceTableService


# 실제 DB 연결 필요 여부 체크
SKIP_LIVE_TESTS = os.getenv("SUPABASE_URL") is None or os.getenv("SUPABASE_KEY") is None


@pytest.fixture
async def supabase_service():
    """실제 Supabase 서비스 인스턴스"""
    if SKIP_LIVE_TESTS:
        pytest.skip("실제 DB 연결 정보가 없습니다 (SUPABASE_URL, SUPABASE_KEY)")

    service = get_supabase_service()
    await service._ensure_initialized()
    return service


@pytest.fixture
async def distance_service(supabase_service):
    """실제 DistanceTableService 인스턴스"""
    return DistanceTableService(supabase_service)


@pytest.mark.skipif(SKIP_LIVE_TESTS, reason="실제 DB 연결 정보 필요")
class TestDistanceTableRebuildLiveVerification:
    """실제 DB를 사용한 Distance Table 재구축 검증"""

    @pytest.mark.asyncio
    async def test_live_verify_thought_units_count(self, supabase_service: SupabaseService):
        """1. thought_units 테이블의 실제 레코드 수 확인"""
        print("\n" + "="*60)
        print("1. thought_units 테이블 레코드 수 확인")
        print("="*60)

        try:
            # thought_units 개수 조회 (embedding NOT NULL)
            response = await (
                supabase_service.client.table("thought_units")
                .select("id", count="exact")
                .not_.is_("embedding", "null")
                .execute()
            )

            total_thoughts = response.count if response.count else 0

            # 예상 페어 수 계산
            expected_pairs = total_thoughts * (total_thoughts - 1) // 2

            print(f"✅ thought_units 개수: {total_thoughts:,}개")
            print(f"✅ 예상 페어 수: {expected_pairs:,}개 (n×(n-1)/2)")
            print("="*60 + "\n")

            # Assertions
            assert total_thoughts > 0, "thought_units가 비어있습니다"

        except Exception as e:
            pytest.fail(f"thought_units 조회 실패: {e}")

    @pytest.mark.asyncio
    async def test_live_verify_distance_table_count(
        self,
        supabase_service: SupabaseService,
        distance_service: DistanceTableService
    ):
        """2. 현재 Distance Table의 페어 수 확인"""
        print("\n" + "="*60)
        print("2. Distance Table 현재 페어 수 확인")
        print("="*60)

        try:
            # Distance Table 통계 조회
            stats = await distance_service.get_statistics()

            current_pairs = stats["total_pairs"]

            # thought_units 개수로 예상 페어 수 계산
            thought_response = await (
                supabase_service.client.table("thought_units")
                .select("id", count="exact")
                .not_.is_("embedding", "null")
                .execute()
            )
            total_thoughts = thought_response.count if thought_response.count else 0
            expected_pairs = total_thoughts * (total_thoughts - 1) // 2

            # 완료율 계산
            completion_rate = (current_pairs / expected_pairs * 100) if expected_pairs > 0 else 0

            print(f"✅ 현재 Distance Table 페어 수: {current_pairs:,}개")
            print(f"✅ 예상 페어 수: {expected_pairs:,}개")
            print(f"✅ 완료율: {completion_rate:.2f}%")
            print(f"✅ 남은 페어: {expected_pairs - current_pairs:,}개")

            # 유사도 통계
            if stats["min_similarity"] is not None:
                print(f"\n유사도 통계:")
                print(f"  - 최소: {stats['min_similarity']:.4f}")
                print(f"  - 최대: {stats['max_similarity']:.4f}")
                print(f"  - 평균: {stats['avg_similarity']:.4f}")

            print("="*60 + "\n")

            # 상태 판단
            if completion_rate >= 99.9:
                print("✅ 상태: 재구축 완료\n")
            elif completion_rate >= 90:
                print("⚠️ 상태: 재구축 거의 완료 (90% 이상)\n")
            elif completion_rate >= 50:
                print("⚠️ 상태: 재구축 진행 중 (50% 이상)\n")
            else:
                print("❌ 상태: 재구축 초기 단계 또는 중단됨 (50% 미만)\n")

            # Assertions
            assert current_pairs >= 0, "Distance Table 페어 수가 음수입니다"

        except Exception as e:
            pytest.fail(f"Distance Table 통계 조회 실패: {e}")

    @pytest.mark.asyncio
    async def test_live_verify_thought_id_ranges(self, supabase_service: SupabaseService):
        """3. thought_a_id, thought_b_id의 범위 확인 (누락된 ID 체크)"""
        print("\n" + "="*60)
        print("3. thought_a_id, thought_b_id 범위 확인")
        print("="*60)

        try:
            # Distance Table에서 ID 범위 조회 (직접 SQL 사용)
            # Supabase는 MIN/MAX aggregation을 지원하지 않으므로 샘플링 사용
            response = await (
                supabase_service.client.table("thought_pair_distances")
                .select("thought_a_id, thought_b_id")
                .order("thought_a_id.asc")
                .limit(1)
                .execute()
            )

            min_a_id = response.data[0]["thought_a_id"] if response.data else None

            response = await (
                supabase_service.client.table("thought_pair_distances")
                .select("thought_a_id, thought_b_id")
                .order("thought_a_id.desc")
                .limit(1)
                .execute()
            )

            max_a_id = response.data[0]["thought_a_id"] if response.data else None

            response = await (
                supabase_service.client.table("thought_pair_distances")
                .select("thought_a_id, thought_b_id")
                .order("thought_b_id.asc")
                .limit(1)
                .execute()
            )

            min_b_id = response.data[0]["thought_b_id"] if response.data else None

            response = await (
                supabase_service.client.table("thought_pair_distances")
                .select("thought_a_id, thought_b_id")
                .order("thought_b_id.desc")
                .limit(1)
                .execute()
            )

            max_b_id = response.data[0]["thought_b_id"] if response.data else None

            # thought_units 최대 ID 조회
            thought_response = await (
                supabase_service.client.table("thought_units")
                .select("id")
                .order("id.desc")
                .limit(1)
                .execute()
            )

            max_thought_id = thought_response.data[0]["id"] if thought_response.data else None

            print(f"✅ thought_a_id 범위: {min_a_id} ~ {max_a_id}")
            print(f"✅ thought_b_id 범위: {min_b_id} ~ {max_b_id}")
            print(f"✅ thought_units 최대 ID: {max_thought_id}")

            # 누락된 ID 범위 확인
            if max_a_id and max_thought_id and max_a_id < max_thought_id:
                missing_start = max_a_id + 1
                missing_end = max_thought_id
                print(f"\n⚠️ 누락된 thought_a_id 범위: {missing_start} ~ {missing_end}")
                print(f"   (재구축이 완료되지 않았을 가능성)")
            else:
                print(f"\n✅ 모든 thought_a_id가 처리된 것으로 보입니다.")

            print("="*60 + "\n")

            # Assertions
            assert min_a_id is not None, "Distance Table에 데이터가 없습니다"

        except Exception as e:
            pytest.fail(f"ID 범위 조회 실패: {e}")

    @pytest.mark.asyncio
    async def test_live_verify_sample_pairs_existence(self, supabase_service: SupabaseService):
        """4. 샘플 페어 존재 여부 확인"""
        print("\n" + "="*60)
        print("4. 샘플 페어 존재 여부 확인")
        print("="*60)

        # 샘플 페어 정의 (thought_a_id, thought_b_id)
        sample_pairs = [
            (1, 2),
            (1, 3),
            (50, 51),
            (100, 101),
            (500, 501),
            (1000, 1001),
            (1500, 1501),
            (1900, 1901)
        ]

        found_count = 0
        missing_pairs = []

        for thought_a, thought_b in sample_pairs:
            try:
                # 개별 페어 조회
                response = await (
                    supabase_service.client.table("thought_pair_distances")
                    .select("thought_a_id, thought_b_id, similarity")
                    .eq("thought_a_id", thought_a)
                    .eq("thought_b_id", thought_b)
                    .limit(1)
                    .execute()
                )

                if response.data and len(response.data) > 0:
                    similarity = response.data[0]["similarity"]
                    print(f"  ✅ 페어 ({thought_a}, {thought_b}): 존재 (similarity={similarity:.4f})")
                    found_count += 1
                else:
                    print(f"  ❌ 페어 ({thought_a}, {thought_b}): 누락")
                    missing_pairs.append((thought_a, thought_b))

            except Exception as e:
                print(f"  ⚠️ 페어 ({thought_a}, {thought_b}): 조회 실패 ({e})")
                missing_pairs.append((thought_a, thought_b))

        print(f"\n✅ 샘플 페어 조회 결과: {found_count}/{len(sample_pairs)} 페어 발견")

        if missing_pairs:
            print(f"⚠️ 누락된 페어: {missing_pairs}")
        else:
            print("✅ 모든 샘플 페어가 존재합니다!")

        print("="*60 + "\n")

        # Assertions
        # 적어도 일부 페어는 존재해야 함
        assert found_count > 0, "샘플 페어를 하나도 찾을 수 없습니다"

    @pytest.mark.asyncio
    async def test_live_rebuild_progress_summary(
        self,
        supabase_service: SupabaseService,
        distance_service: DistanceTableService
    ):
        """5. 재구축 진행 상황 종합 리포트"""
        print("\n" + "="*60)
        print("Distance Table 재구축 진행 상황 종합 리포트")
        print("="*60)

        try:
            # 1. thought_units 개수
            thought_response = await (
                supabase_service.client.table("thought_units")
                .select("id", count="exact")
                .not_.is_("embedding", "null")
                .execute()
            )
            total_thoughts = thought_response.count if thought_response.count else 0

            # 2. 예상 페어 수
            expected_pairs = total_thoughts * (total_thoughts - 1) // 2

            # 3. 현재 페어 수
            stats = await distance_service.get_statistics()
            current_pairs = stats["total_pairs"]

            # 4. 완료율
            completion_rate = (current_pairs / expected_pairs * 100) if expected_pairs > 0 else 0

            # 5. 예상 남은 페어
            remaining_pairs = expected_pairs - current_pairs

            # 종합 리포트 출력
            print(f"\n📊 통계:")
            print(f"  - 총 thought_units: {total_thoughts:,}개")
            print(f"  - 예상 페어 수: {expected_pairs:,}개")
            print(f"  - 현재 페어 수: {current_pairs:,}개")
            print(f"  - 완료율: {completion_rate:.2f}%")
            print(f"  - 남은 페어: {remaining_pairs:,}개")

            # 상태 판단
            print(f"\n📌 상태:")
            if completion_rate >= 99.9:
                print("  ✅ 재구축 완료!")
                status = "completed"
            elif completion_rate >= 90:
                print("  ⚠️ 재구축 거의 완료 (90% 이상)")
                print(f"     예상 남은 시간: ~1-2분")
                status = "near_completion"
            elif completion_rate >= 50:
                print("  ⚠️ 재구축 진행 중 (50% 이상)")
                print(f"     예상 남은 시간: ~3-5분")
                status = "in_progress"
            else:
                print("  ❌ 재구축 초기 단계 또는 중단됨 (50% 미만)")
                print(f"     문제 가능성: 프로세스 중단, 타임아웃, 메모리 부족")
                status = "stalled"

            # 권장 조치
            print(f"\n💡 권장 조치:")
            if completion_rate >= 99.9:
                print("  - 없음. 재구축이 완료되었습니다.")
            elif completion_rate >= 90:
                print("  - 1-2분 대기 후 다시 확인")
                print("  - GET /pipeline/distance-table/status로 진행 상황 모니터링")
            elif completion_rate >= 50:
                print("  - 로그 확인: 배치 실패 또는 타임아웃 메시지")
                print("  - 3-5분 대기 후 다시 확인")
                print("  - 진행이 멈췄다면 POST /pipeline/distance-table/build 재실행")
            else:
                print("  - 로그 확인: 오류 메시지 확인")
                print("  - Supabase 대시보드에서 RPC 함수 상태 확인")
                print("  - POST /pipeline/distance-table/build 재실행 권장")

            print("="*60 + "\n")

            # Assertions
            assert total_thoughts > 0, "thought_units가 비어있습니다"
            assert expected_pairs > 0, "예상 페어 수가 0입니다"

            return {
                "total_thoughts": total_thoughts,
                "expected_pairs": expected_pairs,
                "current_pairs": current_pairs,
                "completion_rate": completion_rate,
                "status": status
            }

        except Exception as e:
            pytest.fail(f"종합 리포트 생성 실패: {e}")


@pytest.mark.skipif(SKIP_LIVE_TESTS, reason="실제 DB 연결 정보 필요")
class TestDistanceTableIntegrityLive:
    """Distance Table 무결성 검사 (실제 DB)"""

    @pytest.mark.asyncio
    async def test_live_check_uniqueness_constraint(self, supabase_service: SupabaseService):
        """UNIQUE constraint 검증: (thought_a_id, thought_b_id) 중복 없음"""
        print("\n" + "="*60)
        print("무결성 검사: UNIQUE constraint")
        print("="*60)

        try:
            # 중복 체크: 같은 (thought_a_id, thought_b_id) 페어가 2개 이상 존재하는지 확인
            # Supabase는 GROUP BY를 지원하지 않으므로 샘플링으로 확인
            response = await (
                supabase_service.client.table("thought_pair_distances")
                .select("thought_a_id, thought_b_id")
                .limit(1000)  # 샘플 1000개
                .execute()
            )

            pairs = [(row["thought_a_id"], row["thought_b_id"]) for row in response.data]
            unique_pairs = set(pairs)

            if len(pairs) != len(unique_pairs):
                duplicates = len(pairs) - len(unique_pairs)
                print(f"❌ 중복 발견: {duplicates}개 (샘플 1000개 중)")
                pytest.fail(f"UNIQUE constraint 위반: {duplicates}개 중복 페어 발견")
            else:
                print(f"✅ 중복 없음 (샘플 {len(pairs)}개 검증)")

            print("="*60 + "\n")

        except Exception as e:
            pytest.fail(f"UNIQUE constraint 검증 실패: {e}")

    @pytest.mark.asyncio
    async def test_live_check_ordering_constraint(self, supabase_service: SupabaseService):
        """CHECK constraint 검증: thought_a_id < thought_b_id"""
        print("\n" + "="*60)
        print("무결성 검사: CHECK constraint (thought_a_id < thought_b_id)")
        print("="*60)

        try:
            # 샘플링으로 정렬 확인
            response = await (
                supabase_service.client.table("thought_pair_distances")
                .select("thought_a_id, thought_b_id")
                .limit(1000)
                .execute()
            )

            violation_count = 0
            for row in response.data:
                if row["thought_a_id"] >= row["thought_b_id"]:
                    violation_count += 1
                    print(f"  ❌ 위반: ({row['thought_a_id']}, {row['thought_b_id']})")

            if violation_count > 0:
                pytest.fail(f"CHECK constraint 위반: {violation_count}개 (샘플 1000개 중)")
            else:
                print(f"✅ 정렬 규칙 준수 (샘플 {len(response.data)}개 검증)")

            print("="*60 + "\n")

        except Exception as e:
            pytest.fail(f"CHECK constraint 검증 실패: {e}")

    @pytest.mark.asyncio
    async def test_live_check_similarity_range(self, supabase_service: SupabaseService):
        """similarity 범위 검증: 0 <= similarity <= 1"""
        print("\n" + "="*60)
        print("무결성 검사: similarity 범위 (0 <= similarity <= 1)")
        print("="*60)

        try:
            # 샘플링으로 범위 확인
            response = await (
                supabase_service.client.table("thought_pair_distances")
                .select("similarity")
                .limit(1000)
                .execute()
            )

            violation_count = 0
            for row in response.data:
                similarity = row["similarity"]
                if similarity < 0 or similarity > 1:
                    violation_count += 1
                    print(f"  ❌ 위반: similarity={similarity}")

            if violation_count > 0:
                pytest.fail(f"similarity 범위 위반: {violation_count}개 (샘플 1000개 중)")
            else:
                print(f"✅ similarity 범위 정상 (샘플 {len(response.data)}개 검증)")

            print("="*60 + "\n")

        except Exception as e:
            pytest.fail(f"similarity 범위 검증 실패: {e}")
