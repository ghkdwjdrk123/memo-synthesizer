"""
Manual test script for Step 4 (Essay generation).

This script performs actual database operations (READ ONLY by default).
Set DRY_RUN=False to actually call the API.

Usage:
    python tests/manual_test_step4.py
"""

import asyncio
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.supabase_service import SupabaseService
from services.ai_service import AIService
from schemas.essay import EssayCreate, UsedThought


DRY_RUN = True  # Set to False to actually generate essays


async def check_database_state():
    """Check current database state."""
    print("\n" + "=" * 60)
    print("1. DATABASE STATE CHECK")
    print("=" * 60)

    service = SupabaseService()

    try:
        # Check unused pairs
        print("\n[1/3] Checking unused thought pairs...")
        unused_pairs = await service.get_unused_thought_pairs(limit=5)
        print(f"✓ Found {len(unused_pairs)} unused pairs")

        if unused_pairs:
            print(f"\nSample pair:")
            pair = unused_pairs[0]
            print(f"  - Pair ID: {pair['id']}")
            print(f"  - Similarity: {pair['similarity_score']:.3f}")
            print(f"  - Connection: {pair['connection_reason'][:100]}...")
        else:
            print("⚠ WARNING: No unused pairs found. Run Step 3 first.")
            return False

        # Check existing essays
        print("\n[2/3] Checking existing essays...")
        essays = await service.get_essays(limit=5)
        print(f"✓ Found {len(essays)} existing essays")

        if essays:
            print(f"\nMost recent essay:")
            essay = essays[0]
            print(f"  - Essay ID: {essay['id']}")
            print(f"  - Title: {essay['title']}")
            print(f"  - Outline items: {len(essay.get('outline', []))}")
            print(f"  - Used thoughts: {len(essay.get('used_thoughts_json', []))}")

        # Check essays table structure
        print("\n[3/3] Verifying essays table structure...")
        if essays and len(essays) > 0:
            required_fields = ['id', 'type', 'title', 'outline', 'used_thoughts_json', 'reason', 'pair_id', 'generated_at']
            missing_fields = [f for f in required_fields if f not in essays[0]]
            if missing_fields:
                print(f"⚠ WARNING: Missing fields: {missing_fields}")
                return False
            else:
                print("✓ All required fields present")

        print("\n✅ Database state check complete")
        return True

    except Exception as e:
        print(f"\n❌ Database check failed: {e}")
        return False


async def test_essay_generation():
    """Test essay generation logic (mock)."""
    print("\n" + "=" * 60)
    print("2. ESSAY GENERATION LOGIC TEST")
    print("=" * 60)

    # Sample pair data (mock)
    pair_data = {
        "pair_id": 999,
        "similarity_score": 0.45,
        "connection_reason": "두 아이디어는 서로 다른 관점에서 같은 주제를 다룹니다.",
        "thought_a": {
            "id": 10,
            "claim": "프로그래밍은 창의적인 문제 해결 과정이다",
            "context": "소프트웨어 개발",
            "source_title": "프로그래밍의 본질",
            "source_url": "https://notion.so/test-page-a"
        },
        "thought_b": {
            "id": 20,
            "claim": "예술은 제약 속에서 피어난다",
            "context": "창작 활동",
            "source_title": "예술과 제약",
            "source_url": "https://notion.so/test-page-b"
        }
    }

    try:
        # Test data structure validation
        print("\n[1/2] Testing data structure...")
        used_thoughts = [
            {
                "thought_id": pair_data["thought_a"]["id"],
                "claim": pair_data["thought_a"]["claim"],
                "source_title": pair_data["thought_a"]["source_title"],
                "source_url": pair_data["thought_a"]["source_url"]
            },
            {
                "thought_id": pair_data["thought_b"]["id"],
                "claim": pair_data["thought_b"]["claim"],
                "source_title": pair_data["thought_b"]["source_title"],
                "source_url": pair_data["thought_b"]["source_url"]
            }
        ]
        print(f"✓ Generated {len(used_thoughts)} used_thoughts")

        # Test Pydantic validation
        print("\n[2/2] Testing Pydantic validation...")
        essay = EssayCreate(
            title="프로그래밍과 예술: 제약 속의 창의성",
            outline=[
                "1단: 프로그래밍에서의 창의적 문제 해결",
                "2단: 예술 창작에서 제약의 긍정적 역할",
                "3단: 두 영역의 공통점 탐구"
            ],
            used_thoughts=[
                UsedThought(**t) for t in used_thoughts
            ],
            reason="서로 다른 영역에서 창의성이 발현되는 메커니즘의 유사성",
            pair_id=999
        )
        print(f"✓ EssayCreate validation passed")
        print(f"  - Title: {essay.title}")
        print(f"  - Outline items: {len(essay.outline)}")
        print(f"  - Used thoughts: {len(essay.used_thoughts)}")

        print("\n✅ Essay generation logic test complete")
        return True

    except Exception as e:
        print(f"\n❌ Essay generation test failed: {e}")
        return False


async def test_actual_generation():
    """Actually generate essays (if DRY_RUN=False)."""
    if DRY_RUN:
        print("\n" + "=" * 60)
        print("3. ACTUAL GENERATION TEST (SKIPPED - DRY_RUN=True)")
        print("=" * 60)
        print("\n⚠ Set DRY_RUN=False in script to actually generate essays")
        return True

    print("\n" + "=" * 60)
    print("3. ACTUAL GENERATION TEST")
    print("=" * 60)

    supabase_service = SupabaseService()
    ai_service = AIService()

    try:
        # Get one unused pair
        print("\n[1/4] Fetching unused pair...")
        unused_pairs = await supabase_service.get_unused_thought_pairs(limit=1)

        if not unused_pairs:
            print("⚠ No unused pairs available")
            return False

        pair = unused_pairs[0]
        print(f"✓ Found pair ID {pair['id']}")

        # Get full pair data
        print("\n[2/4] Fetching pair details...")
        pair_data = await supabase_service.get_pair_with_thoughts(pair['id'])
        print(f"✓ Retrieved pair data")
        print(f"  - Thought A: {pair_data['thought_a']['claim'][:50]}...")
        print(f"  - Thought B: {pair_data['thought_b']['claim'][:50]}...")

        # Generate essay
        print("\n[3/4] Generating essay with Claude...")
        essay_dict = await ai_service.generate_essay(pair_data)
        print(f"✓ Essay generated")
        print(f"  - Title: {essay_dict['title']}")
        print(f"  - Outline: {len(essay_dict['outline'])} items")

        # Save essay
        print("\n[4/4] Saving essay to database...")
        essay = EssayCreate(
            title=essay_dict["title"],
            outline=essay_dict["outline"],
            used_thoughts=essay_dict["used_thoughts"],
            reason=essay_dict["reason"],
            pair_id=pair['id']
        )
        saved_essay = await supabase_service.insert_essay(essay)
        print(f"✓ Essay saved with ID {saved_essay['id']}")

        # Update pair status
        await supabase_service.update_pair_used_status([pair['id']])
        print(f"✓ Pair marked as used")

        print("\n✅ Actual generation test complete")
        return True

    except Exception as e:
        print(f"\n❌ Actual generation test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("STEP 4 (ESSAY GENERATION) MANUAL TEST")
    print("=" * 60)

    results = []

    # Test 1: Database state
    results.append(await check_database_state())

    # Test 2: Logic test
    results.append(await test_essay_generation())

    # Test 3: Actual generation (if enabled)
    results.append(await test_actual_generation())

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    test_names = [
        "Database State Check",
        "Essay Generation Logic",
        "Actual Generation" + (" (skipped)" if DRY_RUN else "")
    ]

    for i, (name, result) in enumerate(zip(test_names, results), 1):
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{i}. {name}: {status}")

    passed = sum(results)
    total = len(results)
    print(f"\nTotal: {passed}/{total} passed")

    if all(results):
        print("\n🎉 All tests passed!")
        return 0
    else:
        print("\n⚠ Some tests failed. Check errors above.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
