"""
Test actual Notion API timestamps vs DB timestamps.
"""
import asyncio
import os
from dotenv import load_dotenv
from services.notion_service import NotionService
from services.supabase_service import SupabaseService

load_dotenv()


async def test_notion_vs_db_timestamps():
    """Compare Notion API timestamps with DB timestamps."""
    print("=" * 80)
    print("Notion API vs DB Timestamp Comparison")
    print("=" * 80)

    notion_service = NotionService()
    supabase_service = SupabaseService()
    await supabase_service._ensure_initialized()

    parent_id = os.getenv("NOTION_PARENT_PAGE_ID")

    # Step 1: Get first page from Notion
    print("\n📡 Fetching pages from Notion API...")
    pages = await notion_service.fetch_child_pages_from_parent(parent_id, page_size=5)
    if not pages:
        print("❌ No pages found")
        return

    first_page = pages[0]
    page_id = first_page["id"]
    notion_timestamp = first_page["last_edited_time"]

    print(f"\n✅ Notion API response:")
    print(f"  Page ID: {page_id}")
    print(f"  last_edited_time: {notion_timestamp}")
    print(f"  Type: {type(notion_timestamp)}")

    # Step 2: Get same page from DB
    print(f"\n🗄️  Fetching same page from DB...")
    try:
        response = await supabase_service.client.table("raw_notes").select(
            "notion_page_id, notion_last_edited_time"
        ).eq("notion_page_id", page_id).execute()

        if not response.data:
            print(f"❌ Page {page_id} not found in DB")
            return

        db_timestamp = response.data[0]["notion_last_edited_time"]
        print(f"\n✅ DB response:")
        print(f"  notion_page_id: {page_id}")
        print(f"  notion_last_edited_time: {db_timestamp}")
        print(f"  Type: {type(db_timestamp)}")

    except Exception as e:
        print(f"\n❌ ERROR fetching from DB: {e}")
        return

    # Step 3: Compare
    print("\n" + "=" * 80)
    print("COMPARISON:")
    print("=" * 80)
    print(f"Notion: {notion_timestamp}")
    print(f"DB:     {db_timestamp}")
    print(f"Match:  {notion_timestamp.replace('Z', '+00:00') == db_timestamp}")

    # Step 4: Test with get_pages_to_fetch
    print("\n" + "=" * 80)
    print("Testing get_pages_to_fetch():")
    print("=" * 80)

    new_ids, updated_ids = await supabase_service.get_pages_to_fetch([first_page])
    print(f"New pages: {len(new_ids)}")
    print(f"Updated pages: {len(updated_ids)}")

    if len(new_ids) == 0 and len(updated_ids) == 0:
        print("\n✅ SUCCESS: Page correctly detected as UNCHANGED")
    else:
        print(f"\n❌ FAILURE: Page incorrectly detected as {'NEW' if page_id in new_ids else 'UPDATED'}")
        print(f"   New IDs: {new_ids}")
        print(f"   Updated IDs: {updated_ids}")

    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(test_notion_vs_db_timestamps())
