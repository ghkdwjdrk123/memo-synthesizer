"""
Check what page IDs Notion API returns
"""
import asyncio
import os
from dotenv import load_dotenv
from services.notion_service import NotionService

load_dotenv()


async def check_notion_page_ids():
    """Check Notion API page ID format."""
    print("=" * 80)
    print("NOTION API PAGE ID CHECK")
    print("=" * 80)

    notion_service = NotionService()
    parent_id = os.getenv("NOTION_PARENT_PAGE_ID")

    print(f"\nFetching pages from parent: {parent_id}")
    print("(This may take a while due to rate limiting...)\n")

    pages = await notion_service.fetch_child_pages_from_parent(parent_id, page_size=5)

    if not pages:
        print("❌ No pages returned from Notion API")
        return

    print(f"✅ Got {len(pages)} pages from Notion API\n")
    print("Sample page IDs (first 3):")

    for i, page in enumerate(pages[:3], 1):
        page_id = page.get("id")
        last_edited = page.get("last_edited_time")
        print(f"\n{i}. Page ID: {page_id}")
        print(f"   Length: {len(page_id)}")
        print(f"   Hyphens: {page_id.count('-')}")
        print(f"   Last Edited: {last_edited}")
        print(f"   Type: {type(last_edited)}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    asyncio.run(check_notion_page_ids())
