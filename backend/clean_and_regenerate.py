"""
기존 데이터 삭제 및 재생성 스크립트
- thought_pairs 삭제
- essays 삭제
- Step 3 재실행 (낮은 유사도 페어 선택)
- Step 4 재실행 (Essay 생성)
"""
import asyncio
import os
from dotenv import load_dotenv
from supabase import create_client, Client
import httpx

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
API_BASE = "http://localhost:8000"

async def main():
    # Supabase 클라이언트 생성
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("=" * 80)
    print("데이터 정리 및 재생성 시작")
    print("=" * 80)

    # 1. 기존 essays 삭제
    print("\n[1] 기존 essays 삭제 중...")
    essays_response = supabase.table("essays").select("id").execute()
    if essays_response.data:
        essay_ids = [e['id'] for e in essays_response.data]
        print(f"   삭제할 essays: {essay_ids}")
        for essay_id in essay_ids:
            supabase.table("essays").delete().eq("id", essay_id).execute()
        print(f"   ✓ {len(essay_ids)}개 essays 삭제 완료")
    else:
        print("   삭제할 essays 없음")

    # 2. 기존 thought_pairs 삭제
    print("\n[2] 기존 thought_pairs 삭제 중...")
    pairs_response = supabase.table("thought_pairs").select("id").execute()
    if pairs_response.data:
        pair_ids = [p['id'] for p in pairs_response.data]
        print(f"   삭제할 pairs: {pair_ids}")
        for pair_id in pair_ids:
            supabase.table("thought_pairs").delete().eq("id", pair_id).execute()
        print(f"   ✓ {len(pair_ids)}개 thought_pairs 삭제 완료")
    else:
        print("   삭제할 pairs 없음")

    # 3. Step 3 재실행 (낮은 유사도 페어 선택)
    print("\n[3] Step 3 재실행 중 (낮은 유사도 페어 선택)...")
    print("   유사도 범위: 0.05 ~ 0.35")

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            response = await client.post(
                f"{API_BASE}/pipeline/select-pairs",
                params={
                    "min_similarity": 0.05,
                    "max_similarity": 0.35,
                    "top_n": 5
                }
            )
            response.raise_for_status()
            result = response.json()
            print(f"   ✓ Step 3 완료: {result.get('selected_pairs', 0)}개 페어 선택됨")
            print(f"   후보 페어: {result.get('total_candidates', 0)}개")
        except Exception as e:
            print(f"   ✗ Step 3 실패: {e}")
            return

    # 4. Step 4 재실행 (Essay 생성)
    print("\n[4] Step 4 재실행 중 (Essay 생성)...")

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            response = await client.post(
                f"{API_BASE}/pipeline/generate-essays",
                params={"count": 3}
            )
            response.raise_for_status()
            result = response.json()
            print(f"   ✓ Step 4 완료: {result.get('generated_essays', 0)}개 essay 생성됨")
        except Exception as e:
            print(f"   ✗ Step 4 실패: {e}")
            return

    # 5. 결과 확인
    print("\n[5] 재생성 결과 확인...")
    print("-" * 80)

    # 새로운 thought_pairs 확인
    new_pairs = supabase.table("thought_pairs").select("*").order("similarity_score").execute()
    if new_pairs.data:
        print(f"\n✓ 새로 생성된 thought_pairs: {len(new_pairs.data)}개")
        for pair in new_pairs.data:
            used_mark = "✓ USED" if pair['is_used_in_essay'] else "○ UNUSED"
            print(f"   ID: {pair['id']:2d} | Similarity: {pair['similarity_score']:.4f} | {used_mark}")

    # 새로운 essays 확인
    new_essays = supabase.table("essays").select("id, title, pair_id").execute()
    if new_essays.data:
        print(f"\n✓ 새로 생성된 essays: {len(new_essays.data)}개")
        for essay in new_essays.data:
            # pair_id로 유사도 찾기
            pair = next((p for p in new_pairs.data if p['id'] == essay['pair_id']), None)
            similarity = pair['similarity_score'] if pair else 'N/A'
            print(f"   Essay ID: {essay['id']:2d} | Pair ID: {essay['pair_id']:2d} | Sim: {similarity:.4f} | {essay['title']}")

    print("\n" + "=" * 80)
    print("재생성 완료!")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())
