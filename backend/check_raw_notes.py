"""
Script to verify raw_notes table after import
"""
import asyncio
from supabase import create_client
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

async def check_raw_notes():
    """Query raw_notes table and provide summary"""
    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("=" * 80)
    print("RAW_NOTES TABLE VERIFICATION")
    print("=" * 80)

    # 1. Get total row count
    print("\n1. Total Row Count:")
    count_result = client.table('raw_notes').select('id', count='exact').execute()
    total_count = count_result.count
    print(f"   Total rows: {total_count}")

    # 2. Get sample of first 5 rows
    print("\n2. Sample of First 5 Rows:")
    sample_result = client.table('raw_notes').select(
        'id, notion_page_id, title, content, notion_created_time, notion_last_edited_time, imported_at'
    ).limit(5).execute()

    for i, row in enumerate(sample_result.data, 1):
        print(f"\n   Row {i}:")
        print(f"   - ID: {row['id']}")
        print(f"   - Notion Page ID: {row['notion_page_id']}")
        print(f"   - Title: {row['title'][:50] if row['title'] else 'NULL'}...")

        content = row.get('content')
        if content is None:
            print(f"   - Content: NULL")
        elif content == "":
            print(f"   - Content: EMPTY STRING")
        else:
            print(f"   - Content Length: {len(content)} characters")
            print(f"   - Content Preview: {content[:100]}...")

        print(f"   - Created Time: {row['notion_created_time']}")
        print(f"   - Last Edited: {row['notion_last_edited_time']}")
        print(f"   - Imported At: {row['imported_at']}")

    # 3. Check content field statistics
    print("\n3. Content Field Statistics:")

    # Count NULL content
    null_result = client.table('raw_notes').select('id', count='exact').is_('content', 'null').execute()
    null_count = null_result.count
    print(f"   - NULL content: {null_count} rows ({null_count/total_count*100:.1f}%)")

    # Count empty string content
    empty_result = client.table('raw_notes').select('id', count='exact').eq('content', '').execute()
    empty_count = empty_result.count
    print(f"   - Empty string content: {empty_count} rows ({empty_count/total_count*100:.1f}%)")

    # Count populated content
    populated_count = total_count - null_count - empty_count
    print(f"   - Populated content: {populated_count} rows ({populated_count/total_count*100:.1f}%)")

    # 4. Verify timestamps
    print("\n4. Timestamp Verification:")

    # Check for NULL timestamps
    null_created = client.table('raw_notes').select('id', count='exact').is_('notion_created_time', 'null').execute()
    null_edited = client.table('raw_notes').select('id', count='exact').is_('notion_last_edited_time', 'null').execute()

    print(f"   - NULL notion_created_time: {null_created.count} rows")
    print(f"   - NULL notion_last_edited_time: {null_edited.count} rows")

    # Get timestamp range
    time_range = client.table('raw_notes').select(
        'notion_created_time, notion_last_edited_time'
    ).order('notion_created_time').limit(1).execute()

    if time_range.data:
        print(f"   - Earliest created_time: {time_range.data[0]['notion_created_time']}")

    time_range_latest = client.table('raw_notes').select(
        'notion_created_time, notion_last_edited_time'
    ).order('notion_created_time', desc=True).limit(1).execute()

    if time_range_latest.data:
        print(f"   - Latest created_time: {time_range_latest.data[0]['notion_created_time']}")

    # 5. Check properties_json
    print("\n5. Properties JSON:")
    props_sample = client.table('raw_notes').select('properties_json').limit(3).execute()

    for i, row in enumerate(props_sample.data, 1):
        props = row.get('properties_json', {})
        print(f"   Row {i} properties keys: {list(props.keys()) if props else 'Empty'}")
        if props and '본문' in props:
            bon_mun = props['본문']
            print(f"      - 본문 type: {type(bon_mun)}")
            if isinstance(bon_mun, str):
                print(f"      - 본문 length: {len(bon_mun)}")

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY:")
    print("=" * 80)
    print(f"Expected rows: 724")
    print(f"Actual rows: {total_count}")
    print(f"Status: {'✓ SUCCESS' if total_count == 724 else '✗ MISMATCH'}")

    if populated_count > 0:
        print(f"Content: ✓ {populated_count} rows have content")
    else:
        print(f"Content: ✗ No rows have content (likely using properties_json['본문'])")

    if null_created.count == 0 and null_edited.count == 0:
        print("Timestamps: ✓ All timestamps populated")
    else:
        print("Timestamps: ✗ Some timestamps missing")

    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(check_raw_notes())
