"""
Debug script to check page ID format and DB comparison
"""
import asyncio
import os
from dotenv import load_dotenv
from services.notion_service import NotionService
from services.supabase_service import SupabaseService

load_dotenv()


async def debug_page_id_format():
    """Compare Notion API page IDs with DB page IDs."""
    print("=" * 80)
    print("PAGE ID FORMAT COMPARISON")
    print("=" * 80)

    notion_service = NotionService()
    supabase_service = SupabaseService()
    await supabase_service._ensure_initialized()

    parent_id = os.getenv("NOTION_PARENT_PAGE_ID")

    # Step 1: Get pages from Notion API
    print("\n📡 Fetching pages from Notion API...")
    notion_pages = await notion_service.fetch_child_pages_from_parent(parent_id, page_size=5)

    if not notion_pages:
        print("❌ No pages from Notion API")
        return

    print(f"✅ Got {len(notion_pages)} pages from Notion API\n")

    # Step 2: Get pages from DB
    print("🗄️  Fetching pages from DB...")
    db_response = await supabase_service.client.table("raw_notes").select(
        "notion_page_id, notion_last_edited_time"
    ).limit(5).execute()

    if not db_response.data:
        print("❌ No pages in DB")
        return

    print(f"✅ Got {len(db_response.data)} pages from DB\n")

    # Step 3: Compare formats
    print("=" * 80)
    print("FORMAT COMPARISON:")
    print("=" * 80)

    print("\nNotion API page IDs (first 3):")
    for i, page in enumerate(notion_pages[:3], 1):
        page_id = page.get("id")
        last_edited = page.get("last_edited_time")
        print(f"  {i}. ID: {page_id}")
        print(f"     Length: {len(page_id)}")
        print(f"     Hyphens: {page_id.count('-')}")
        print(f"     Last Edited: {last_edited}")
        print(f"     Type: {type(last_edited)}")

    print("\nDB notion_page_ids (first 3):")
    for i, row in enumerate(db_response.data[:3], 1):
        page_id = row["notion_page_id"]
        last_edited = row["notion_last_edited_time"]
        print(f"  {i}. ID: {page_id}")
        print(f"     Length: {len(page_id)}")
        print(f"     Hyphens: {page_id.count('-')}")
        print(f"     Last Edited: {last_edited}")
        print(f"     Type: {type(last_edited)}")

    # Step 4: Check if any Notion page IDs exist in DB
    print("\n" + "=" * 80)
    print("CROSS-CHECK: Do Notion page IDs exist in DB?")
    print("=" * 80)

    notion_page_ids = [p["id"] for p in notion_pages]
    print(f"\nQuerying DB with {len(notion_page_ids)} Notion page IDs...")

    db_match_response = await supabase_service.client.table("raw_notes").select(
        "notion_page_id, notion_last_edited_time"
    ).in_("notion_page_id", notion_page_ids).execute()

    print(f"DB returned {len(db_match_response.data)} matching rows")

    if len(db_match_response.data) == 0:
        print("\n❌ CRITICAL: No matches found!")
        print("   This means Notion API page IDs DO NOT match DB page IDs")

        print("\n🔍 Detailed comparison:")
        notion_id = notion_pages[0]["id"]
        db_id = db_response.data[0]["notion_page_id"]

        print(f"\n   Notion ID: '{notion_id}'")
        print(f"   DB ID:     '{db_id}'")
        print(f"\n   Character-by-character comparison:")
        max_len = max(len(notion_id), len(db_id))
        for i in range(max_len):
            n_char = notion_id[i] if i < len(notion_id) else "END"
            d_char = db_id[i] if i < len(db_id) else "END"
            match = "✓" if n_char == d_char else "✗"
            print(f"      Pos {i:2d}: '{n_char}' vs '{d_char}' {match}")
    else:
        print(f"\n✅ Found {len(db_match_response.data)} matches")
        for row in db_match_response.data[:3]:
            print(f"   - {row['notion_page_id']}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    asyncio.run(debug_page_id_format())
