"""
Step 3 검증: 유사도 분포 확인

thought_units 간 유사도 분포를 분석하여 0.3-0.7 범위 내 후보 쌍 개수 확인
"""

import asyncio
import os
import sys
from pathlib import Path

# Add backend to path
backend_path = Path(__file__).parent.parent.parent / "backend"
sys.path.insert(0, str(backend_path))

from services.supabase_service import SupabaseService


async def main():
    """유사도 분포 확인"""
    print("=== Step 3 검증: 유사도 분포 확인 ===\n")

    # Initialize Supabase service
    supabase = SupabaseService()
    await supabase._ensure_initialized()

    try:
        # Get all thought_units with embeddings
        response = await supabase.client.table("thought_units")\
            .select("id, claim, embedding")\
            .not_.is_("embedding", "null")\
            .execute()

        thoughts = response.data
        print(f"✓ thought_units with embeddings: {len(thoughts)}")

        if len(thoughts) < 2:
            print("\n⚠️  경고: 사고 단위가 2개 미만입니다. Step 3 실행 불가.")
            return

        # Calculate total possible pairs
        total_pairs = len(thoughts) * (len(thoughts) - 1) // 2
        print(f"✓ 가능한 쌍 개수: C({len(thoughts)}, 2) = {total_pairs}")

        # Test the stored procedure with different ranges
        print("\n--- Stored Procedure 테스트 ---")

        # Test 1: 0.3-0.7 range
        try:
            result = await supabase.client.rpc(
                "find_similar_pairs",
                {"min_sim": 0.3, "max_sim": 0.7, "lim": 100}
            ).execute()

            candidates_0307 = result.data
            print(f"✓ 0.3-0.7 범위 후보 쌍: {len(candidates_0307)}개")

            if len(candidates_0307) > 0:
                similarities = [c["similarity_score"] for c in candidates_0307]
                print(f"  - 최소 유사도: {min(similarities):.3f}")
                print(f"  - 최대 유사도: {max(similarities):.3f}")
                print(f"  - 평균 유사도: {sum(similarities)/len(similarities):.3f}")

                # Show top 3 pairs
                print(f"\n  상위 3개 쌍:")
                for i, c in enumerate(candidates_0307[:3], 1):
                    print(f"  {i}. ID {c['thought_a_id']} ↔ {c['thought_b_id']} (유사도: {c['similarity_score']:.3f})")
                    print(f"     A: {c['thought_a_claim'][:50]}...")
                    print(f"     B: {c['thought_b_claim'][:50]}...")

        except Exception as e:
            print(f"✗ Stored Procedure 호출 실패: {e}")
            if "function find_similar_pairs" in str(e).lower():
                print("\n⚠️  Stored Procedure가 생성되지 않았습니다.")
                print("   docs/supabase_setup.sql의 find_similar_pairs() 함수를 Supabase에서 실행하세요.")
            return

        # Test 2: Broader range to see distribution
        result_broad = await supabase.client.rpc(
            "find_similar_pairs",
            {"min_sim": 0.0, "max_sim": 1.0, "lim": 100}
        ).execute()

        all_candidates = result_broad.data
        print(f"\n✓ 전체 범위 (0.0-1.0) 후보 쌍: {len(all_candidates)}개")

        if len(all_candidates) > 0:
            all_sims = [c["similarity_score"] for c in all_candidates]
            print(f"  - 최소 유사도: {min(all_sims):.3f}")
            print(f"  - 최대 유사도: {max(all_sims):.3f}")
            print(f"  - 평균 유사도: {sum(all_sims)/len(all_sims):.3f}")

            # Distribution analysis
            ranges = {
                "0.0-0.3": 0,
                "0.3-0.5": 0,
                "0.5-0.7": 0,
                "0.7-1.0": 0
            }

            for sim in all_sims:
                if sim < 0.3:
                    ranges["0.0-0.3"] += 1
                elif sim < 0.5:
                    ranges["0.3-0.5"] += 1
                elif sim < 0.7:
                    ranges["0.5-0.7"] += 1
                else:
                    ranges["0.7-1.0"] += 1

            print(f"\n  유사도 분포:")
            for range_name, count in ranges.items():
                percentage = (count / len(all_sims) * 100) if all_sims else 0
                print(f"    {range_name}: {count}개 ({percentage:.1f}%)")

        # Recommendation
        print(f"\n--- 권장사항 ---")
        if len(candidates_0307) == 0:
            print("⚠️  0.3-0.7 범위에 후보 쌍이 없습니다.")
            print("   범위를 조정하거나 더 많은 메모를 추가하세요.")
        elif len(candidates_0307) < 5:
            print(f"⚠️  후보 쌍이 {len(candidates_0307)}개로 적습니다.")
            print(f"   top_n을 {len(candidates_0307)} 이하로 설정하세요.")
        else:
            print(f"✓ Step 3 실행 가능합니다. ({len(candidates_0307)}개 후보 쌍)")
            print(f"   권장 top_n: {min(5, len(candidates_0307))}")

    finally:
        await supabase.close()


if __name__ == "__main__":
    asyncio.run(main())
