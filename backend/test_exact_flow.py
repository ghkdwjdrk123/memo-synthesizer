"""
Test exact flow of get_pages_to_fetch() with real data
"""
import asyncio
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_async_client

load_dotenv()


async def test_exact_flow():
    """Replicate the exact flow of get_pages_to_fetch()"""
    print("=" * 80)
    print("EXACT FLOW TEST")
    print("=" * 80)

    # Step 1: Initialize async client (same as SupabaseService)
    client = await create_async_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_KEY")
    )

    # Step 2: Create fake Notion pages (using real format from API)
    notion_pages = [
        {
            "id": "556603bc-bad1-4f3a-af19-64619edbe24c",
            "last_edited_time": "2026-01-15T07:42:00.000Z"
        },
        {
            "id": "255b94e5-8350-49a3-a03f-57fa98bde45c",
            "last_edited_time": "2023-02-28T15:50:00.000Z"
        },
        {
            "id": "84f78b4c-3d22-42fa-99f6-f34beb84452d",
            "last_edited_time": "2023-04-10T17:45:00.000Z"
        }
    ]

    print(f"\n1. Processing {len(notion_pages)} Notion pages...")

    # Step 3: Build page_map (exact same logic as get_pages_to_fetch)
    page_map = {}
    for p in notion_pages:
        page_id = p.get("id")
        last_edited = p.get("last_edited_time")

        if not page_id or not last_edited:
            print(f"   ⚠️  Page missing id or last_edited_time: {p}")
            continue

        try:
            notion_time = datetime.fromisoformat(last_edited.replace("Z", "+00:00"))
            notion_time = notion_time.replace(microsecond=0)
            page_map[page_id] = notion_time
            print(f"   ✓ Parsed: {page_id[:20]}... → {notion_time}")
        except (ValueError, AttributeError) as e:
            print(f"   ✗ Failed to parse: {page_id}: {e}")
            continue

    print(f"\n2. Built page_map with {len(page_map)} entries")

    # Step 4: Query DB (exact same query)
    BATCH_SIZE = 1000
    page_ids = list(page_map.keys())
    existing_map = {}

    print(f"\n3. Querying DB with {len(page_ids)} page IDs...")

    try:
        for i in range(0, len(page_ids), BATCH_SIZE):
            batch_ids = page_ids[i:i+BATCH_SIZE]
            print(f"   Batch {i//BATCH_SIZE + 1}: {len(batch_ids)} IDs")

            response = await (
                client.table("raw_notes")
                .select("notion_page_id, notion_last_edited_time")
                .in_("notion_page_id", batch_ids)
                .execute()
            )

            print(f"   → DB returned {len(response.data)} rows")

            if len(response.data) == 0:
                print("   ❌ CRITICAL: DB query returned 0 rows!")
                print(f"      Batch IDs were: {batch_ids}")
            else:
                print(f"   ✅ Got {len(response.data)} rows from DB")

            for row in response.data:
                db_page_id = row["notion_page_id"]
                db_time = row["notion_last_edited_time"]

                print(f"      Row: {db_page_id[:20]}... → {db_time} (type: {type(db_time)})")

                # Parse DB timestamp (exact same logic)
                if isinstance(db_time, str):
                    db_time = datetime.fromisoformat(db_time.replace("Z", "+00:00"))

                if db_time.tzinfo is None:
                    db_time = db_time.replace(tzinfo=timezone.utc)

                db_time = db_time.replace(microsecond=0)
                existing_map[db_page_id] = db_time

    except Exception as e:
        print(f"   ❌ Exception during DB query: {e}")
        import traceback
        traceback.print_exc()

    print(f"\n4. Built existing_map with {len(existing_map)} entries")

    # Step 5: Compare (exact same logic)
    new_page_ids = []
    updated_page_ids = []

    print(f"\n5. Comparing timestamps...")

    for page_id, notion_time in page_map.items():
        if page_id not in existing_map:
            new_page_ids.append(page_id)
            print(f"   NEW: {page_id[:20]}... (not in DB)")
        else:
            db_time = existing_map[page_id]
            print(f"   Comparing {page_id[:20]}...")
            print(f"      Notion: {notion_time}")
            print(f"      DB:     {db_time}")

            if notion_time > db_time:
                updated_page_ids.append(page_id)
                print(f"      → UPDATED (Notion newer)")
            else:
                print(f"      → UNCHANGED (same or older)")

    # Step 6: Results
    print("\n" + "=" * 80)
    print("RESULTS:")
    print("=" * 80)
    print(f"New pages: {len(new_page_ids)}")
    print(f"Updated pages: {len(updated_page_ids)}")
    print(f"Unchanged pages: {len(page_map) - len(new_page_ids) - len(updated_page_ids)}")

    if len(new_page_ids) > 0:
        print(f"\nNew page IDs: {new_page_ids}")
    if len(updated_page_ids) > 0:
        print(f"Updated page IDs: {updated_page_ids}")

    # Expected: All 3 should be "unchanged" since they're in DB
    expected_unchanged = 3
    actual_unchanged = len(page_map) - len(new_page_ids) - len(updated_page_ids)

    if actual_unchanged == expected_unchanged:
        print(f"\n✅ TEST PASSED: All {expected_unchanged} pages detected as unchanged")
    else:
        print(f"\n❌ TEST FAILED: Expected {expected_unchanged} unchanged, got {actual_unchanged}")

    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(test_exact_flow())
