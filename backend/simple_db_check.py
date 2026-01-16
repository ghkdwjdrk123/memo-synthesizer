"""
Simple synchronous DB check
"""
import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

print("=" * 80)
print("SIMPLE DB CHECK")
print("=" * 80)

client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Get 3 page IDs from DB
print("\n1. Getting 3 page IDs from DB...")
db_result = client.table("raw_notes").select("notion_page_id").limit(3).execute()
db_page_ids = [row["notion_page_id"] for row in db_result.data]

print(f"   Found {len(db_page_ids)} page IDs:")
for i, page_id in enumerate(db_page_ids, 1):
    print(f"   {i}. {page_id} (len={len(page_id)})")

# Try to query with IN clause using those same IDs
print(f"\n2. Querying DB with those same {len(db_page_ids)} page IDs using IN clause...")
test_result = client.table("raw_notes").select(
    "notion_page_id, notion_last_edited_time"
).in_("notion_page_id", db_page_ids).execute()

print(f"   Query returned {len(test_result.data)} rows")

if len(test_result.data) == len(db_page_ids):
    print("   ✅ SUCCESS: IN clause works correctly")
    for row in test_result.data:
        print(f"      - {row['notion_page_id']}: {row['notion_last_edited_time']}")
else:
    print(f"   ❌ FAILURE: Expected {len(db_page_ids)} rows, got {len(test_result.data)}")

print("\n" + "=" * 80)
