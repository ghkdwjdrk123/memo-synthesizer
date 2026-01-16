"""
Detailed analysis of properties_json content
"""
import asyncio
from supabase import create_client
import os
from dotenv import load_dotenv
import json

# Load environment variables
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

async def analyze_properties():
    """Analyze properties_json structure and content"""
    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("=" * 80)
    print("PROPERTIES_JSON DETAILED ANALYSIS")
    print("=" * 80)

    # Get 10 samples with properties
    samples = client.table('raw_notes').select(
        'id, notion_page_id, title, properties_json'
    ).limit(10).execute()

    print("\n1. Sample Properties Structure:")
    for i, row in enumerate(samples.data, 1):
        props = row.get('properties_json', {})
        print(f"\n   Sample {i}:")
        print(f"   - Title: {row['title'][:60]}...")

        if '본문' in props:
            bon_mun = props['본문']
            print(f"   - 본문 length: {len(bon_mun)} chars")
            print(f"   - 본문 preview: {bon_mun[:150]}...")

        if '키워드' in props:
            keywords = props['키워드']
            print(f"   - 키워드: {keywords}")

    # Statistics on content length
    print("\n2. Content Length Statistics:")
    all_rows = client.table('raw_notes').select('properties_json').execute()

    lengths = []
    empty_content = 0
    missing_bonmun = 0

    for row in all_rows.data:
        props = row.get('properties_json', {})
        if '본문' not in props:
            missing_bonmun += 1
            continue

        bon_mun = props['본문']
        if not bon_mun or bon_mun.strip() == "":
            empty_content += 1
        else:
            lengths.append(len(bon_mun))

    if lengths:
        print(f"   - Rows with 본문: {len(lengths)}")
        print(f"   - Rows with empty 본문: {empty_content}")
        print(f"   - Rows missing 본문: {missing_bonmun}")
        print(f"   - Min length: {min(lengths)} chars")
        print(f"   - Max length: {max(lengths)} chars")
        print(f"   - Average length: {sum(lengths)/len(lengths):.1f} chars")
        print(f"   - Median length: {sorted(lengths)[len(lengths)//2]} chars")

    # Check if content should be migrated from properties_json to content field
    print("\n3. Recommendation:")
    if missing_bonmun == 0 and len(lengths) > 0:
        print("   ✓ All rows have '본문' in properties_json")
        print("   ✓ Data structure is consistent")
        print("   Note: Content is stored in properties_json['본문'], not in 'content' field")
        print("   This is the expected behavior for Parent Page mode import.")
    else:
        print("   ✗ Some rows missing '본문' field")
        print(f"   Missing: {missing_bonmun} rows")

    # Show full JSON structure for one row
    print("\n4. Full JSON Structure (Sample):")
    sample_full = client.table('raw_notes').select('properties_json').limit(1).execute()
    if sample_full.data:
        print(json.dumps(sample_full.data[0]['properties_json'], indent=2, ensure_ascii=False))

    print("\n" + "=" * 80)

if __name__ == "__main__":
    asyncio.run(analyze_properties())
