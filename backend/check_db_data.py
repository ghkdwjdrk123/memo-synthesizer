"""
현재 DB의 thought_pairs와 essays 데이터 확인 스크립트
"""
import asyncio
import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

async def main():
    # Supabase 클라이언트 생성
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("=" * 80)
    print("현재 DB 상태 확인")
    print("=" * 80)

    # 1. thought_pairs 조회
    print("\n[1] thought_pairs 테이블 (유사도 순)")
    print("-" * 80)
    pairs_response = supabase.table("thought_pairs").select("*").order("similarity_score").execute()

    if pairs_response.data:
        print(f"총 {len(pairs_response.data)}개 페어 발견\n")
        for pair in pairs_response.data:
            used_mark = "✓ USED" if pair['is_used_in_essay'] else "○ UNUSED"
            print(f"ID: {pair['id']:2d} | Similarity: {pair['similarity_score']:.4f} | {used_mark}")
            if pair.get('connection_reason'):
                print(f"      Reason: {pair['connection_reason'][:80]}...")
            print()
    else:
        print("페어 없음")

    # 2. essays 조회
    print("\n[2] essays 테이블")
    print("-" * 80)
    essays_response = supabase.table("essays").select("id, title, pair_id, generated_at").execute()

    if essays_response.data:
        print(f"총 {len(essays_response.data)}개 에세이 발견\n")
        for essay in essays_response.data:
            print(f"Essay ID: {essay['id']:2d} | Pair ID: {essay['pair_id']:2d} | {essay['title']}")
            print(f"      Generated: {essay['generated_at']}")
            print()
    else:
        print("에세이 없음")

    # 3. 유사도 분포 분석
    print("\n[3] 유사도 분포 분석")
    print("-" * 80)
    if pairs_response.data:
        scores = [p['similarity_score'] for p in pairs_response.data]
        print(f"최소 유사도: {min(scores):.4f}")
        print(f"최대 유사도: {max(scores):.4f}")
        print(f"평균 유사도: {sum(scores)/len(scores):.4f}")

        # 범위별 분포
        ranges = [
            (0.00, 0.10, "0.00-0.10"),
            (0.10, 0.20, "0.10-0.20"),
            (0.20, 0.30, "0.20-0.30"),
            (0.30, 0.40, "0.30-0.40"),
            (0.40, 0.50, "0.40-0.50"),
            (0.50, 1.00, "0.50-1.00"),
        ]

        print("\n유사도 범위별 분포:")
        for min_val, max_val, label in ranges:
            count = sum(1 for s in scores if min_val <= s < max_val)
            if count > 0:
                print(f"  {label}: {count}개")

    print("\n" + "=" * 80)
    print("분석 완료")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())
