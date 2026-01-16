"""
Cross-check: Do Notion API page IDs exist in DB?
"""
import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

print("=" * 80)
print("CROSS-CHECK: Notion API page IDs vs DB")
print("=" * 80)

client = create_client(SUPABASE_URL, SUPABASE_KEY)

# These are the page IDs from Notion API
notion_page_ids = [
    "556603bc-bad1-4f3a-af19-64619edbe24c",
    "255b94e5-8350-49a3-a03f-57fa98bde45c",
    "84f78b4c-3d22-42fa-99f6-f34beb84452d"
]

print(f"\n1. Looking for {len(notion_page_ids)} Notion page IDs in DB...")
for i, page_id in enumerate(notion_page_ids, 1):
    print(f"   {i}. {page_id}")

# Query DB with those IDs
result = client.table("raw_notes").select(
    "notion_page_id, notion_last_edited_time"
).in_("notion_page_id", notion_page_ids).execute()

print(f"\n2. DB query result: {len(result.data)} rows returned")

if len(result.data) == 0:
    print("\n❌ CRITICAL PROBLEM: No matches found!")
    print("\nThis means Notion API page IDs are NOT in the DB.")
    print("Let me check if the format differs...\n")

    # Get some DB page IDs for comparison
    db_sample = client.table("raw_notes").select("notion_page_id").limit(5).execute()
    print("Sample DB page IDs:")
    for i, row in enumerate(db_sample.data, 1):
        print(f"   {i}. {row['notion_page_id']}")

elif len(result.data) == len(notion_page_ids):
    print("\n✅ SUCCESS: All Notion page IDs found in DB!")
    for row in result.data:
        print(f"   - {row['notion_page_id']}: {row['notion_last_edited_time']}")
else:
    print(f"\n⚠️  PARTIAL MATCH: Found {len(result.data)} out of {len(notion_page_ids)}")
    print("Matches:")
    for row in result.data:
        print(f"   - {row['notion_page_id']}")

print("\n" + "=" * 80)
