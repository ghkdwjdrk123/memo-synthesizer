"""
Step 3 검증 스크립트

thought_pairs 테이블 데이터를 상세 분석하여 Step 3 완료 확인
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
    """Step 3 검증"""
    print("=" * 70)
    print("Step 3 검증: thought_pairs 테이블 데이터 분석")
    print("=" * 70)

    supabase = SupabaseService()
    await supabase._ensure_initialized()

    try:
        # 1. thought_pairs 통계
        print("\n[1] thought_pairs 테이블 통계")
        print("-" * 70)

        response = await supabase.client.table("thought_pairs").select("*").execute()
        pairs = response.data

        print(f"✓ 총 페어 개수: {len(pairs)}")

        if len(pairs) == 0:
            print("\n⚠️  저장된 페어가 없습니다. Step 3를 먼저 실행하세요.")
            return

        # 유사도 분석
        similarities = [p["similarity_score"] for p in pairs]
        print(f"✓ 유사도 범위: {min(similarities):.3f} - {max(similarities):.3f}")
        print(f"✓ 평균 유사도: {sum(similarities)/len(similarities):.3f}")

        # is_used_in_essay 통계
        used_count = sum(1 for p in pairs if p["is_used_in_essay"])
        unused_count = len(pairs) - used_count
        print(f"✓ 사용된 페어: {used_count}개")
        print(f"✓ 미사용 페어: {unused_count}개")

        # 2. 상위 5개 페어 상세 정보
        print("\n[2] 상위 5개 페어 (유사도 기준)")
        print("-" * 70)

        sorted_pairs = sorted(pairs, key=lambda x: x["similarity_score"], reverse=True)

        for i, pair in enumerate(sorted_pairs[:5], 1):
            # thought_units 정보 가져오기
            thought_a_response = await supabase.client.table("thought_units")\
                .select("claim, raw_note_id")\
                .eq("id", pair["thought_a_id"])\
                .single()\
                .execute()

            thought_b_response = await supabase.client.table("thought_units")\
                .select("claim, raw_note_id")\
                .eq("id", pair["thought_b_id"])\
                .single()\
                .execute()

            thought_a = thought_a_response.data
            thought_b = thought_b_response.data

            # raw_notes 정보 가져오기
            note_a_response = await supabase.client.table("raw_notes")\
                .select("title")\
                .eq("id", thought_a["raw_note_id"])\
                .single()\
                .execute()

            note_b_response = await supabase.client.table("raw_notes")\
                .select("title")\
                .eq("id", thought_b["raw_note_id"])\
                .single()\
                .execute()

            note_a = note_a_response.data
            note_b = note_b_response.data

            print(f"\n{i}. Pair ID: {pair['id']} (유사도: {pair['similarity_score']:.3f})")
            print(f"   사용 여부: {'✓ 사용됨' if pair['is_used_in_essay'] else '○ 미사용'}")
            print(f"\n   [Thought A - ID {pair['thought_a_id']}]")
            print(f"   출처: {note_a['title']}")
            print(f"   Claim: {thought_a['claim'][:100]}{'...' if len(thought_a['claim']) > 100 else ''}")
            print(f"\n   [Thought B - ID {pair['thought_b_id']}]")
            print(f"   출처: {note_b['title']}")
            print(f"   Claim: {thought_b['claim'][:100]}{'...' if len(thought_b['claim']) > 100 else ''}")
            print(f"\n   [연결 이유]")
            print(f"   {pair['connection_reason'][:200]}{'...' if len(pair['connection_reason']) > 200 else ''}")

        # 3. 데이터 무결성 검증
        print("\n[3] 데이터 무결성 검증")
        print("-" * 70)

        issues = []

        for pair in pairs:
            # thought_a_id < thought_b_id 검증
            if pair["thought_a_id"] >= pair["thought_b_id"]:
                issues.append(f"Pair {pair['id']}: thought_a_id ({pair['thought_a_id']}) >= thought_b_id ({pair['thought_b_id']})")

            # similarity_score 범위 검증
            if not (0 <= pair["similarity_score"] <= 1):
                issues.append(f"Pair {pair['id']}: similarity_score ({pair['similarity_score']}) out of range [0, 1]")

            # connection_reason 길이 검증
            if not pair["connection_reason"] or len(pair["connection_reason"]) < 10:
                issues.append(f"Pair {pair['id']}: connection_reason too short (< 10 chars)")

            if len(pair["connection_reason"]) > 500:
                issues.append(f"Pair {pair['id']}: connection_reason too long (> 500 chars)")

        if issues:
            print(f"✗ 발견된 문제: {len(issues)}개")
            for issue in issues[:5]:  # 최대 5개만 표시
                print(f"  - {issue}")
        else:
            print("✓ 데이터 무결성 검증 통과")

        # 4. 중복 페어 검증
        print("\n[4] 중복 페어 검증")
        print("-" * 70)

        pair_tuples = [(p["thought_a_id"], p["thought_b_id"]) for p in pairs]
        duplicates = [t for t in pair_tuples if pair_tuples.count(t) > 1]

        if duplicates:
            print(f"✗ 중복된 페어 발견: {len(set(duplicates))}개")
            for dup in list(set(duplicates))[:5]:
                print(f"  - thought_a_id={dup[0]}, thought_b_id={dup[1]}")
        else:
            print("✓ 중복 페어 없음")

        # 5. Step 4 준비 상태
        print("\n[5] Step 4 준비 상태")
        print("-" * 70)

        if unused_count > 0:
            print(f"✓ Step 4 실행 가능: {unused_count}개의 미사용 페어")
            print(f"  권장: /pipeline/generate-essays 엔드포인트 구현 후 실행")
        else:
            print("⚠️  모든 페어가 사용됨. 새로운 페어 생성 또는 기존 페어 재사용 필요")

        # 6. 요약
        print("\n" + "=" * 70)
        print("검증 요약")
        print("=" * 70)
        print(f"✓ 총 페어: {len(pairs)}개")
        print(f"✓ 유사도 범위: {min(similarities):.3f} - {max(similarities):.3f}")
        print(f"✓ 평균 유사도: {sum(similarities)/len(similarities):.3f}")
        print(f"✓ 미사용 페어: {unused_count}개")
        print(f"✓ 무결성 이슈: {len(issues)}개")
        print(f"✓ 중복 페어: {len(set(duplicates))}개")

        if len(issues) == 0 and len(duplicates) == 0 and unused_count > 0:
            print("\n🎉 Step 3 검증 완료! 다음 단계(Step 4)로 진행 가능합니다.")
        else:
            print("\n⚠️  일부 문제가 발견되었습니다. 위 내용을 확인하세요.")

    finally:
        await supabase.close()


if __name__ == "__main__":
    asyncio.run(main())
