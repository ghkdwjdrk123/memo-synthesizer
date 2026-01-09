"""
유사도 분포 분석 스크립트

모든 가능한 thought_unit 쌍의 유사도를 분석하여 분포를 확인
"""

import asyncio
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add backend to path
backend_path = Path(__file__).parent.parent.parent / "backend"
sys.path.insert(0, str(backend_path))

# Load environment variables
env_path = backend_path / ".env"
load_dotenv(env_path)

from services.supabase_service import SupabaseService


async def main():
    """유사도 분포 분석"""
    print("=" * 70)
    print("유사도 분포 분석")
    print("=" * 70)

    supabase = SupabaseService()
    await supabase._ensure_initialized()

    try:
        # 1. 전체 유사도 분포 가져오기
        print("\n[1] 전체 유사도 분포")
        print("-" * 70)

        result = await supabase.client.rpc(
            "find_similar_pairs",
            {"min_sim": 0.0, "max_sim": 1.0, "lim": 1000}
        ).execute()

        all_pairs = result.data
        total_count = len(all_pairs)

        print(f"✓ 분석 가능한 총 쌍: {total_count}개")

        if total_count == 0:
            print("\n⚠️  thought_units가 충분하지 않습니다. (최소 2개 필요)")
            return

        # 2. 유사도 통계
        print("\n[2] 유사도 통계")
        print("-" * 70)

        similarities = [p["similarity_score"] for p in all_pairs]
        similarities.sort()

        print(f"✓ 최소 유사도: {min(similarities):.4f}")
        print(f"✓ 최대 유사도: {max(similarities):.4f}")
        print(f"✓ 평균 유사도: {sum(similarities)/len(similarities):.4f}")
        print(f"✓ 중간값: {similarities[len(similarities)//2]:.4f}")

        # 사분위수
        q1_idx = len(similarities) // 4
        q3_idx = (3 * len(similarities)) // 4
        print(f"✓ 1사분위수 (Q1): {similarities[q1_idx]:.4f}")
        print(f"✓ 3사분위수 (Q3): {similarities[q3_idx]:.4f}")

        # 3. 유사도 범위별 분포
        print("\n[3] 유사도 범위별 분포")
        print("-" * 70)

        ranges = {
            "0.0-0.2": (0.0, 0.2),
            "0.2-0.3": (0.2, 0.3),
            "0.3-0.4": (0.3, 0.4),
            "0.4-0.5": (0.4, 0.5),
            "0.5-0.6": (0.5, 0.6),
            "0.6-0.7": (0.6, 0.7),
            "0.7-0.8": (0.7, 0.8),
            "0.8-1.0": (0.8, 1.0),
        }

        print(f"{'범위':<12} {'개수':>6} {'비율':>7} {'막대 그래프'}")
        print("-" * 70)

        for range_name, (min_val, max_val) in ranges.items():
            count = sum(1 for s in similarities if min_val <= s < max_val)
            percentage = (count / total_count * 100) if total_count > 0 else 0
            bar_length = int(percentage / 2)  # 2% per character
            bar = "█" * bar_length

            print(f"{range_name:<12} {count:>6} {percentage:>6.1f}%  {bar}")

        # 4. 약한 연결(0.05-0.35) 범위 분석 (수정: 낮은 유사도 = 서로 다른 아이디어)
        print("\n[4] 약한 연결 범위 (0.05-0.35) 분석 - 서로 다른 아이디어 연결")
        print("-" * 70)

        weak_connection = [s for s in similarities if 0.05 <= s <= 0.35]
        weak_count = len(weak_connection)
        weak_percentage = (weak_count / total_count * 100) if total_count > 0 else 0

        print(f"✓ 0.05-0.35 범위 쌍: {weak_count}개 ({weak_percentage:.1f}%)")

        if weak_count > 0:
            print(f"✓ 범위 내 최소: {min(weak_connection):.4f}")
            print(f"✓ 범위 내 최대: {max(weak_connection):.4f}")
            print(f"✓ 범위 내 평균: {sum(weak_connection)/len(weak_connection):.4f}")
        else:
            print("⚠️  0.05-0.35 범위에 쌍이 없습니다.")

        # 5. 가장 유사한/먼 쌍 출력
        print("\n[5] 가장 유사한 쌍 (Top 3)")
        print("-" * 70)

        sorted_pairs = sorted(all_pairs, key=lambda x: x["similarity_score"], reverse=True)

        for i, pair in enumerate(sorted_pairs[:3], 1):
            print(f"\n{i}. 유사도: {pair['similarity_score']:.4f}")
            print(f"   Thought A (ID {pair['thought_a_id']}): {pair['thought_a_claim'][:80]}...")
            print(f"   Thought B (ID {pair['thought_b_id']}): {pair['thought_b_claim'][:80]}...")

        print("\n[6] 가장 먼 쌍 (Bottom 3)")
        print("-" * 70)

        for i, pair in enumerate(sorted_pairs[-3:], 1):
            print(f"\n{i}. 유사도: {pair['similarity_score']:.4f}")
            print(f"   Thought A (ID {pair['thought_a_id']}): {pair['thought_a_claim'][:80]}...")
            print(f"   Thought B (ID {pair['thought_b_id']}): {pair['thought_b_claim'][:80]}...")

        # 6. 권장사항
        print("\n[7] 권장사항")
        print("-" * 70)

        if weak_count == 0:
            print("⚠️  0.05-0.35 범위에 후보 쌍이 없습니다.")
            print("   → 범위를 조정하거나 더 많은 메모를 추가하세요.")

            # 대안 범위 제안
            if total_count > 0:
                q1 = similarities[q1_idx]
                q3 = similarities[q3_idx]
                print(f"\n   대안 범위 제안:")
                print(f"   - Q1-Q3 범위: {q1:.2f}-{q3:.2f} ({sum(1 for s in similarities if q1 <= s <= q3)}개)")

                # 30% 정도를 포함하는 범위 찾기
                target_count = int(total_count * 0.3)
                mid_idx = len(similarities) // 2
                half_range = target_count // 2

                suggested_min = similarities[max(0, mid_idx - half_range)]
                suggested_max = similarities[min(len(similarities) - 1, mid_idx + half_range)]
                suggested_count = sum(1 for s in similarities if suggested_min <= s <= suggested_max)

                print(f"   - 중앙 30% 범위: {suggested_min:.2f}-{suggested_max:.2f} ({suggested_count}개)")

        elif weak_count < 5:
            print(f"⚠️  후보 쌍이 {weak_count}개로 적습니다.")
            print(f"   → top_n을 {weak_count} 이하로 설정하세요.")
        else:
            print(f"✓ Step 3 실행 가능합니다. ({weak_count}개 후보 쌍)")
            print(f"   → 권장 top_n: {min(5, weak_count)}")

        # 7. 요약
        print("\n" + "=" * 70)
        print("분석 요약")
        print("=" * 70)
        print(f"✓ 총 쌍 개수: {total_count}")
        print(f"✓ 유사도 범위: {min(similarities):.4f} - {max(similarities):.4f}")
        print(f"✓ 평균 유사도: {sum(similarities)/len(similarities):.4f}")
        print(f"✓ 0.05-0.35 범위 (낮은 유사도): {weak_count}개 ({weak_percentage:.1f}%)")

    finally:
        await supabase.close()


if __name__ == "__main__":
    asyncio.run(main())
