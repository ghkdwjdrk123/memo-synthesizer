"""
Test incremental import functionality.
"""
import requests
import time
import json

BASE_URL = "http://localhost:8000"


def test_case_1_reimport_without_changes():
    """
    Test Case 1: Re-import without changes
    Expected: 0 imported, 726 skipped
    """
    print("=" * 80)
    print("Test Case 1: Re-import Without Changes")
    print("=" * 80)

    # Start import
    response = requests.post(f"{BASE_URL}/pipeline/import-from-notion?page_size=100")
    data = response.json()
    job_id = data["job_id"]

    print(f"✅ Import job started: {job_id}")
    print()

    # Poll job status
    while True:
        time.sleep(2)
        status_response = requests.get(f"{BASE_URL}/pipeline/import-status/{job_id}")
        status_data = status_response.json()

        status = status_data["status"]
        processed = status_data["processed_pages"]
        total = status_data["total_pages"]
        imported = status_data["imported_pages"]
        skipped = status_data["skipped_pages"]

        print(f"Status: {status} | Progress: {processed}/{total} | Imported: {imported} | Skipped: {skipped}", end="\r")

        if status in ["completed", "failed"]:
            print()
            break

    # Final results
    print()
    print("=" * 80)
    print("RESULTS:")
    print("=" * 80)
    print(f"Status: {status_data['status']}")
    print(f"Total pages: {status_data['total_pages']}")
    print(f"Processed: {status_data['processed_pages']}")
    print(f"Imported: {status_data['imported_pages']}")
    print(f"Skipped: {status_data['skipped_pages']}")
    print(f"Failed: {len(status_data.get('failed_pages', []))}")
    print()

    # Verify
    expected_imported = 0
    expected_skipped = 726

    if status_data["imported_pages"] == expected_imported and status_data["skipped_pages"] == expected_skipped:
        print("✅ TEST PASSED: All pages skipped (no changes detected)")
    else:
        print(f"❌ TEST FAILED: Expected {expected_imported} imported, {expected_skipped} skipped")
        print(f"             Got {status_data['imported_pages']} imported, {status_data['skipped_pages']} skipped")

    print("=" * 80)
    return status_data


if __name__ == "__main__":
    try:
        result = test_case_1_reimport_without_changes()
        print(json.dumps(result, indent=2))
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
    except Exception as e:
        print(f"\n\n❌ TEST ERROR: {e}")
        import traceback
        traceback.print_exc()
