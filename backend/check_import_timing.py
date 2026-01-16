"""
Check when data was imported to understand timestamp issues
"""
import os
from dotenv import load_dotenv
from supabase import create_client
from datetime import datetime

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

print("=" * 80)
print("IMPORT TIMING ANALYSIS")
print("=" * 80)

client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Get the 3 test pages
test_page_ids = [
    "556603bc-bad1-4f3a-af19-64619edbe24c",
    "255b94e5-8350-49a3-a03f-57fa98bde45c",
    "84f78b4c-3d22-42fa-99f6-f34beb84452d"
]

result = client.table("raw_notes").select(
    "notion_page_id, notion_last_edited_time, imported_at"
).in_("notion_page_id", test_page_ids).execute()

print(f"\nFound {len(result.data)} pages in DB:\n")

for row in result.data:
    print(f"Page: {row['notion_page_id']}")
    print(f"  notion_last_edited_time: {row['notion_last_edited_time']}")
    print(f"  imported_at:             {row['imported_at']}")
    print()

# Get import statistics
print("=" * 80)
print("IMPORT STATISTICS")
print("=" * 80)

# Most recent import
recent = client.table("raw_notes").select("imported_at").order("imported_at", desc=True).limit(1).execute()
if recent.data:
    print(f"\nMost recent import: {recent.data[0]['imported_at']}")

# Oldest import
oldest = client.table("raw_notes").select("imported_at").order("imported_at").limit(1).execute()
if oldest.data:
    print(f"Oldest import:      {oldest.data[0]['imported_at']}")

# Count by import date
print("\nImport batches (group by date):")
all_imports = client.table("raw_notes").select("imported_at").execute()

# Group by date (hour precision)
import_times = {}
for row in all_imports.data:
    imported_at = row['imported_at']
    if isinstance(imported_at, str):
        dt = datetime.fromisoformat(imported_at.replace("Z", "+00:00"))
    else:
        dt = imported_at

    date_hour = dt.strftime("%Y-%m-%d %H:00")
    import_times[date_hour] = import_times.get(date_hour, 0) + 1

print("\nImport counts by hour:")
for date_hour in sorted(import_times.keys(), reverse=True):
    print(f"  {date_hour}: {import_times[date_hour]} pages")

print("\n" + "=" * 80)
