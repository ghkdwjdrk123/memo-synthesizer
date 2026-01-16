"""
Verify if Notion page IDs are stable across API calls
"""
import asyncio
import os
from dotenv import load_dotenv
from services.notion_service import NotionService
from supabase import create_client

load_dotenv()


async def verify_page_id_stability():
    """Check if API page IDs match DB page IDs"""
    print("=" * 80)
    print("PAGE ID STABILITY CHECK")
    print("=" * 80)

    # Step 1: Get pages from API
    notion_service = NotionService()
    parent_id = os.getenv("NOTION_PARENT_PAGE_ID")

    print("\n1. Fetching ALL pages from Notion API...")
    print("   (This will take several minutes due to rate limiting...)\n")

    api_pages = await notion_service.fetch_child_pages_from_parent(parent_id, page_size=100)
    api_page_ids = set(p["id"] for p in api_pages)

    print(f"   ✅ Got {len(api_page_ids)} pages from API")

    # Step 2: Get pages from DB
    client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

    print("\n2. Fetching ALL pages from DB...")
    db_result = client.table("raw_notes").select("notion_page_id").execute()
    db_page_ids = set(row["notion_page_id"] for row in db_result.data)

    print(f"   ✅ Got {len(db_page_ids)} pages from DB")

    # Step 3: Compare
    print("\n" + "=" * 80)
    print("COMPARISON:")
    print("=" * 80)

    in_api_not_db = api_page_ids - db_page_ids
    in_db_not_api = db_page_ids - api_page_ids
    in_both = api_page_ids & db_page_ids

    print(f"\nIn API only (NEW pages):     {len(in_api_not_db)}")
    print(f"In DB only (DELETED pages):  {len(in_db_not_api)}")
    print(f"In BOTH (EXISTING pages):    {len(in_both)}")

    if len(in_api_not_db) > 0:
        print(f"\n📌 NEW pages in API (sample):")
        for page_id in list(in_api_not_db)[:5]:
            print(f"   - {page_id}")

    if len(in_db_not_api) > 0:
        print(f"\n🗑️  DELETED pages (in DB but not in API) (sample):")
        for page_id in list(in_db_not_api)[:5]:
            print(f"   - {page_id}")

    # Step 4: Analysis
    print("\n" + "=" * 80)
    print("ANALYSIS:")
    print("=" * 80)

    if len(in_api_not_db) == len(api_page_ids):
        print("\n❌ CRITICAL: NO pages match!")
        print("   This means page IDs have completely changed or there's a format issue.")
    elif len(in_api_not_db) > 0:
        print(f"\n✅ NORMAL: {len(in_api_not_db)} new pages since last import")
        print(f"   Incremental update will handle these correctly.")
    else:
        print(f"\n✅ PERFECT: All API pages exist in DB")
        print(f"   No new pages since last import.")

    if len(in_db_not_api) > 0:
        print(f"\n⚠️  WARNING: {len(in_db_not_api)} pages in DB not found in API")
        print(f"   These may have been deleted or archived in Notion.")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    asyncio.run(verify_page_id_stability())
