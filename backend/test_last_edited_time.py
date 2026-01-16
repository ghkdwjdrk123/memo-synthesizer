"""
🔴 CRITICAL TEST: Verify last_edited_time reflects content changes

This test verifies whether Notion's blocks.children.list() API returns
accurate last_edited_time that reflects page content changes.

If this test FAILS:
- Incremental update plan is NOT viable
- Must use Alternative Plan B (pages.retrieve API)

If this test PASSES:
- Incremental update plan is viable
- Proceed with implementation
"""

import os
import asyncio
from dotenv import load_dotenv
from services.notion_service import NotionService

load_dotenv()


async def test_last_edited_time_accuracy():
    """
    Test whether last_edited_time from blocks.children.list()
    reflects page content changes.
    """
    print("=" * 80)
    print("🔴 CRITICAL TEST: last_edited_time Accuracy Verification")
    print("=" * 80)
    print()

    service = NotionService()
    parent_id = os.getenv("NOTION_PARENT_PAGE_ID")

    if not parent_id:
        print("❌ ERROR: NOTION_PARENT_PAGE_ID not set in .env")
        return

    print(f"📋 Parent Page ID: {parent_id}")
    print()

    # Step 1: Get first page
    print("Step 1: Fetching child pages...")
    try:
        pages = await service.fetch_child_pages_from_parent(parent_id, page_size=5)
    except Exception as e:
        print(f"❌ ERROR: Failed to fetch pages: {e}")
        return

    if not pages:
        print("❌ ERROR: No pages found")
        return

    print(f"✅ Found {len(pages)} pages")
    print()

    # Select first page for testing
    page = pages[0]
    page_id = page["id"]
    page_title = page.get("properties", {}).get("제목", "Untitled")
    initial_time = page["last_edited_time"]
    page_url = f"https://notion.so/{page_id.replace('-', '')}"

    print("=" * 80)
    print("📄 Selected Test Page:")
    print(f"  Page ID: {page_id}")
    print(f"  Title: {page_title}")
    print(f"  Initial last_edited_time: {initial_time}")
    print(f"  URL: {page_url}")
    print("=" * 80)
    print()

    # Step 2: Prompt user to edit
    print("⚠️  ACTION REQUIRED:")
    print("=" * 80)
    print("1. Open the URL above in your browser")
    print("2. Edit the page content (add any text)")
    print("3. Wait for Notion to save (check for 'Saved' indicator)")
    print("4. Return here and press Enter")
    print("=" * 80)
    print()

    input("Press Enter after editing the page... ")
    print()

    # Step 3: Wait a bit for Notion to update
    print("⏳ Waiting 3 seconds for Notion to propagate changes...")
    await asyncio.sleep(3)
    print()

    # Step 4: Fetch again
    print("Step 2: Re-fetching child pages...")
    try:
        pages_after = await service.fetch_child_pages_from_parent(
            parent_id,
            page_size=100
        )
    except Exception as e:
        print(f"❌ ERROR: Failed to re-fetch pages: {e}")
        return

    # Find the same page
    page_after = None
    for p in pages_after:
        if p["id"] == page_id:
            page_after = p
            break

    if not page_after:
        print(f"❌ ERROR: Could not find page {page_id} in second fetch")
        return

    final_time = page_after["last_edited_time"]

    print(f"✅ Re-fetched successfully")
    print()

    # Step 5: Compare timestamps
    print("=" * 80)
    print("🔍 RESULT ANALYSIS:")
    print("=" * 80)
    print(f"  Initial last_edited_time: {initial_time}")
    print(f"  Final last_edited_time:   {final_time}")
    print(f"  Changed: {initial_time != final_time}")
    print("=" * 80)
    print()

    # Step 6: Verdict
    if initial_time == final_time:
        print("❌" * 40)
        print("❌ CRITICAL: TIMESTAMP NOT UPDATED!")
        print("❌" * 40)
        print()
        print("📋 VERDICT:")
        print("  - last_edited_time does NOT reflect content changes")
        print("  - blocks.children.list() API is NOT suitable for change detection")
        print("  - Incremental update Plan A is NOT viable")
        print()
        print("🔄 NEXT STEPS:")
        print("  1. Use Alternative Plan B (pages.retrieve API)")
        print("  2. Expected performance: ~4 minutes (vs 9 minutes current)")
        print("  3. Trade-off: More API calls, but still 2x faster")
        print()
        return False

    else:
        print("✅" * 40)
        print("✅ SUCCESS: TIMESTAMP UPDATED CORRECTLY!")
        print("✅" * 40)
        print()
        print("📋 VERDICT:")
        print("  - last_edited_time DOES reflect content changes")
        print("  - blocks.children.list() API is suitable for change detection")
        print("  - Incremental update Plan A is VIABLE")
        print()
        print("🚀 NEXT STEPS:")
        print("  1. Proceed with Plan A implementation")
        print("  2. Expected performance: 99% API reduction")
        print("  3. Example: 10 changes = 3 seconds (vs 9 minutes)")
        print()
        return True


async def main():
    """Main test runner."""
    try:
        result = await test_last_edited_time_accuracy()

        print("=" * 80)
        if result:
            print("✅ TEST PASSED - Plan A is viable")
        else:
            print("❌ TEST FAILED - Switch to Plan B")
        print("=" * 80)

    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
    except Exception as e:
        print(f"\n\n❌ TEST ERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
