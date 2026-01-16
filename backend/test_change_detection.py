"""
Test change detection logic directly.
"""
import asyncio
import os
from dotenv import load_dotenv
from services.supabase_service import SupabaseService

load_dotenv()


async def test_change_detection():
    """Test the get_pages_to_fetch method."""
    print("=" * 80)
    print("Testing Change Detection Logic")
    print("=" * 80)

    service = SupabaseService()
    await service._ensure_initialized()

    # Step 1: Check how many pages in DB
    try:
        response = await service.client.table("raw_notes").select("notion_page_id, notion_last_edited_time").limit(10).execute()
        print(f"\n✅ Found {len(response.data)} pages in DB (sample)")
        if response.data:
            print("\nSample page:")
            print(f"  notion_page_id: {response.data[0]['notion_page_id']}")
            print(f"  notion_last_edited_time: {response.data[0]['notion_last_edited_time']}")
            print(f"  Type: {type(response.data[0]['notion_last_edited_time'])}")
    except Exception as e:
        print(f"\n❌ ERROR fetching from DB: {e}")
        return

    # Step 2: Create fake Notion pages
    # Use the EXACT timestamp from DB
    db_timestamp = response.data[0]["notion_last_edited_time"]
    print(f"\n📝 DB timestamp: {db_timestamp} (type: {type(db_timestamp)})")

    fake_pages = [
        {
            "id": response.data[0]["notion_page_id"],
            "last_edited_time": db_timestamp.replace("+00:00", "Z").replace(" ", "T")  # Convert to Notion format
        },
        {
            "id": "new-page-123",
            "last_edited_time": "2026-01-15T08:00:00.000Z"  # New page
        }
    ]

    print(f"📝 Notion timestamp (converted): {fake_pages[0]['last_edited_time']}")

    print("\n" + "=" * 80)
    print("Testing with 2 fake pages:")
    print(f"  1. Existing page: {fake_pages[0]['id'][:20]}...")
    print(f"  2. New page: {fake_pages[1]['id']}")
    print("=" * 80)

    # Step 3: Test change detection
    try:
        new_ids, updated_ids = await service.get_pages_to_fetch(fake_pages)
        print(f"\n✅ Change detection completed:")
        print(f"  New pages: {len(new_ids)}")
        print(f"  Updated pages: {len(updated_ids)}")

        if new_ids:
            print(f"\n  New page IDs: {new_ids}")
        if updated_ids:
            print(f"  Updated page IDs: {updated_ids}")

    except Exception as e:
        print(f"\n❌ ERROR in change detection: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 80)


if __name__ == "__main__":
    asyncio.run(test_change_detection())
