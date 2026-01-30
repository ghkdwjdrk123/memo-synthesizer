"""
Distance Table 재구축 진행 상황 검증 스크립트.

실행 방법:
    python scripts/verify_distance_table_rebuild.py

환경변수 필요:
    SUPABASE_URL, SUPABASE_KEY
"""

import asyncio
import sys
import os

# Add backend directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.supabase_service import get_supabase_service
from services.distance_table_service import DistanceTableService


async def verify_thought_units_count(supabase_service):
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

        return total_thoughts, expected_pairs

    except Exception as e:
        print(f"❌ thought_units 조회 실패: {e}")
        return None, None


async def verify_distance_table_count(supabase_service, distance_service, expected_pairs):
    """2. 현재 Distance Table의 페어 수 확인"""
    print("\n" + "="*60)
    print("2. Distance Table 현재 페어 수 확인")
    print("="*60)

    try:
        # Distance Table 통계 조회
        stats = await distance_service.get_statistics()

        current_pairs = stats["total_pairs"]

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
            status = "completed"
        elif completion_rate >= 90:
            print("⚠️ 상태: 재구축 거의 완료 (90% 이상)\n")
            status = "near_completion"
        elif completion_rate >= 50:
            print("⚠️ 상태: 재구축 진행 중 (50% 이상)\n")
            status = "in_progress"
        else:
            print("❌ 상태: 재구축 초기 단계 또는 중단됨 (50% 미만)\n")
            status = "stalled"

        return current_pairs, completion_rate, status

    except Exception as e:
        print(f"❌ Distance Table 통계 조회 실패: {e}")
        return None, None, None


async def verify_id_ranges(supabase_service):
    """3. thought_a_id, thought_b_id의 범위 확인"""
    print("\n" + "="*60)
    print("3. thought_a_id, thought_b_id 범위 확인")
    print("="*60)

    try:
        # MIN/MAX ID 조회 (order 문법 수정)
        response = await (
            supabase_service.client.table("thought_pair_distances")
            .select("thought_a_id, thought_b_id")
            .order("thought_a_id", desc=False)
            .limit(1)
            .execute()
        )
        min_a_id = response.data[0]["thought_a_id"] if response.data else None

        response = await (
            supabase_service.client.table("thought_pair_distances")
            .select("thought_a_id, thought_b_id")
            .order("thought_a_id", desc=True)
            .limit(1)
            .execute()
        )
        max_a_id = response.data[0]["thought_a_id"] if response.data else None

        response = await (
            supabase_service.client.table("thought_pair_distances")
            .select("thought_a_id, thought_b_id")
            .order("thought_b_id", desc=False)
            .limit(1)
            .execute()
        )
        min_b_id = response.data[0]["thought_b_id"] if response.data else None

        response = await (
            supabase_service.client.table("thought_pair_distances")
            .select("thought_a_id, thought_b_id")
            .order("thought_b_id", desc=True)
            .limit(1)
            .execute()
        )
        max_b_id = response.data[0]["thought_b_id"] if response.data else None

        # thought_units 최대 ID 조회
        thought_response = await (
            supabase_service.client.table("thought_units")
            .select("id")
            .order("id", desc=True)
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

        return max_a_id, max_thought_id

    except Exception as e:
        print(f"❌ ID 범위 조회 실패: {e}")
        return None, None


async def verify_sample_pairs(supabase_service):
    """4. 샘플 페어 존재 여부 확인"""
    print("\n" + "="*60)
    print("4. 샘플 페어 존재 여부 확인")
    print("="*60)

    # 샘플 페어 정의
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

    return found_count, len(sample_pairs)


async def generate_summary_report(total_thoughts, expected_pairs, current_pairs, completion_rate, status):
    """5. 종합 리포트 생성"""
    print("\n" + "="*60)
    print("Distance Table 재구축 진행 상황 종합 리포트")
    print("="*60)

    remaining_pairs = expected_pairs - current_pairs

    print(f"\n📊 통계:")
    print(f"  - 총 thought_units: {total_thoughts:,}개")
    print(f"  - 예상 페어 수: {expected_pairs:,}개")
    print(f"  - 현재 페어 수: {current_pairs:,}개")
    print(f"  - 완료율: {completion_rate:.2f}%")
    print(f"  - 남은 페어: {remaining_pairs:,}개")

    print(f"\n📌 상태:")
    if status == "completed":
        print("  ✅ 재구축 완료!")
    elif status == "near_completion":
        print("  ⚠️ 재구축 거의 완료 (90% 이상)")
        print(f"     예상 남은 시간: ~1-2분")
    elif status == "in_progress":
        print("  ⚠️ 재구축 진행 중 (50% 이상)")
        print(f"     예상 남은 시간: ~3-5분")
    else:
        print("  ❌ 재구축 초기 단계 또는 중단됨 (50% 미만)")
        print(f"     문제 가능성: 프로세스 중단, 타임아웃, 메모리 부족")

    print(f"\n💡 권장 조치:")
    if status == "completed":
        print("  - 없음. 재구축이 완료되었습니다.")
    elif status == "near_completion":
        print("  - 1-2분 대기 후 다시 확인")
        print("  - GET /pipeline/distance-table/status로 진행 상황 모니터링")
    elif status == "in_progress":
        print("  - 로그 확인: 배치 실패 또는 타임아웃 메시지")
        print("  - 3-5분 대기 후 다시 확인")
        print("  - 진행이 멈췄다면 POST /pipeline/distance-table/build 재실행")
    else:
        print("  - 로그 확인: 오류 메시지 확인")
        print("  - Supabase 대시보드에서 RPC 함수 상태 확인")
        print("  - POST /pipeline/distance-table/build 재실행 권장")

    print("="*60 + "\n")


async def main():
    """메인 검증 프로세스"""
    print("\n🔍 Distance Table 재구축 검증 시작...\n")

    # 서비스 초기화
    supabase_service = get_supabase_service()
    await supabase_service._ensure_initialized()
    distance_service = DistanceTableService(supabase_service)

    # 1. thought_units 개수 확인
    total_thoughts, expected_pairs = await verify_thought_units_count(supabase_service)

    if total_thoughts is None:
        print("❌ 검증 실패: thought_units 조회 불가")
        return

    # 2. Distance Table 현재 상태 확인
    current_pairs, completion_rate, status = await verify_distance_table_count(
        supabase_service, distance_service, expected_pairs
    )

    if current_pairs is None:
        print("❌ 검증 실패: Distance Table 조회 불가")
        return

    # 3. ID 범위 확인
    max_a_id, max_thought_id = await verify_id_ranges(supabase_service)

    # 4. 샘플 페어 확인
    found_count, total_samples = await verify_sample_pairs(supabase_service)

    # 5. 종합 리포트
    await generate_summary_report(
        total_thoughts, expected_pairs, current_pairs, completion_rate, status
    )

    print("✅ Distance Table 재구축 검증 완료\n")


if __name__ == "__main__":
    asyncio.run(main())
