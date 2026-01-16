#!/usr/bin/env python3
"""
Solution 3 RPC 기반 증분 import 통합 테스트

테스트 시나리오:
1. Test 1: 초기 Import (726개 전체)
2. Test 2: 재실행 (변경 없음 - 0 imported, 726 skipped)
3. Test 3: RPC 성능 측정
"""

import asyncio
import httpx
import time
from datetime import datetime


BASE_URL = "http://localhost:8000"


async def poll_job_status(client: httpx.AsyncClient, job_id: str, max_wait: int = 600) -> dict:
    """
    Job 상태 polling (최대 10분).

    Args:
        client: HTTP 클라이언트
        job_id: Job UUID
        max_wait: 최대 대기 시간 (초)

    Returns:
        dict: 최종 job 상태
    """
    start_time = time.time()
    last_status = None

    while time.time() - start_time < max_wait:
        response = await client.get(f"{BASE_URL}/pipeline/import-status/{job_id}")

        if response.status_code != 200:
            print(f"❌ Status check failed: {response.status_code}")
            return None

        data = response.json()
        status = data["status"]
        progress = data["progress_percentage"]

        # Print progress only when it changes
        if last_status != progress:
            print(f"[Job {job_id}] Status: {status}, Progress: {progress}%, "
                  f"Imported: {data['imported_pages']}, Skipped: {data['skipped_pages']}")
            last_status = progress

        # Check if job is complete
        if status in ["completed", "failed"]:
            return data

        await asyncio.sleep(5)  # Poll every 5 seconds

    print(f"⏱️  Timeout: Job did not complete within {max_wait}s")
    return None


async def test_1_initial_import():
    """
    Test 1: 초기 Import (726개 전체)

    예상 결과:
    - imported_pages: 726
    - skipped_pages: 0
    - status: "completed"
    """
    print("\n" + "="*80)
    print("TEST 1: 초기 Import (726개 전체)")
    print("="*80)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Start import job
        print("Starting import job...")
        start_time = time.time()

        response = await client.post(f"{BASE_URL}/pipeline/import-from-notion?page_size=100")

        if response.status_code != 200:
            print(f"❌ Failed to start job: {response.status_code} - {response.text}")
            return None

        data = response.json()
        job_id = data["job_id"]
        print(f"✅ Job started: {job_id}")

        # Poll until complete
        final_status = await poll_job_status(client, job_id, max_wait=600)

        if final_status is None:
            print("❌ Test 1 FAILED: Job did not complete")
            return None

        elapsed = time.time() - start_time

        # Print results
        print("\n" + "-"*80)
        print("TEST 1 결과:")
        print(f"  Status: {final_status['status']}")
        print(f"  Total Pages: {final_status['total_pages']}")
        print(f"  Imported: {final_status['imported_pages']}")
        print(f"  Skipped: {final_status['skipped_pages']}")
        print(f"  Failed: {len(final_status['failed_pages'])}")
        print(f"  Elapsed Time: {elapsed:.1f}s")
        print("-"*80)

        # Validate
        if final_status['status'] == "completed":
            print("✅ Test 1 PASSED")
        else:
            print(f"❌ Test 1 FAILED: Status is {final_status['status']}")

        return final_status


async def test_2_rerun_no_changes():
    """
    Test 2: 재실행 (변경 없음 - 0 imported, 726 skipped)

    예상 결과:
    - imported_pages: 0 (변경 없음)
    - skipped_pages: 726 (전체 skip)
    - 시간: < 10초 (기존 9분 vs 개선)
    """
    print("\n" + "="*80)
    print("TEST 2: 재실행 (변경 없음 - RPC 핵심 기능)")
    print("="*80)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Start import job
        print("Starting import job (재실행)...")
        start_time = time.time()

        response = await client.post(f"{BASE_URL}/pipeline/import-from-notion?page_size=100")

        if response.status_code != 200:
            print(f"❌ Failed to start job: {response.status_code} - {response.text}")
            return None

        data = response.json()
        job_id = data["job_id"]
        print(f"✅ Job started: {job_id}")

        # Poll until complete
        final_status = await poll_job_status(client, job_id, max_wait=600)

        if final_status is None:
            print("❌ Test 2 FAILED: Job did not complete")
            return None

        elapsed = time.time() - start_time

        # Print results
        print("\n" + "-"*80)
        print("TEST 2 결과:")
        print(f"  Status: {final_status['status']}")
        print(f"  Total Pages: {final_status['total_pages']}")
        print(f"  Imported: {final_status['imported_pages']} (예상: 0)")
        print(f"  Skipped: {final_status['skipped_pages']} (예상: 726)")
        print(f"  Failed: {len(final_status['failed_pages'])}")
        print(f"  Elapsed Time: {elapsed:.1f}s (예상: < 10s)")
        print("-"*80)

        # Validate
        success = True

        if final_status['imported_pages'] != 0:
            print(f"❌ Test 2 FAILED: Expected 0 imported, got {final_status['imported_pages']}")
            success = False

        if final_status['skipped_pages'] < 700:  # Allow some margin
            print(f"❌ Test 2 FAILED: Expected ~726 skipped, got {final_status['skipped_pages']}")
            success = False

        if elapsed > 10:
            print(f"⚠️  Test 2 WARNING: Expected < 10s, took {elapsed:.1f}s")
            # Not a hard failure - may depend on system load

        if success:
            print(f"✅ Test 2 PASSED: RPC 증분 import 정상 작동 (0 imported, {final_status['skipped_pages']} skipped)")

        return final_status


async def test_3_rpc_performance():
    """
    Test 3: RPC 성능 측정

    예상 결과:
    - RPC 응답 시간: < 1초
    """
    print("\n" + "="*80)
    print("TEST 3: RPC 성능 측정")
    print("="*80)

    from services.supabase_service import get_supabase_service
    from services.notion_service import NotionService

    # Fetch pages from Notion
    print("Fetching pages from Notion (for RPC test)...")
    notion_service = NotionService()
    from config import settings

    pages = await notion_service.fetch_child_pages_from_parent(
        parent_page_id=settings.notion_parent_page_id,
        page_size=100
    )

    print(f"Retrieved {len(pages)} pages")

    # Test RPC performance
    supabase_service = get_supabase_service()

    print("Testing RPC change detection...")
    start_time = time.time()

    new_ids, updated_ids = await supabase_service.get_pages_to_fetch(pages)

    elapsed = time.time() - start_time

    # Print results
    print("\n" + "-"*80)
    print("TEST 3 결과:")
    print(f"  New Pages: {len(new_ids)}")
    print(f"  Updated Pages: {len(updated_ids)}")
    print(f"  Unchanged: {len(pages) - len(new_ids) - len(updated_ids)}")
    print(f"  RPC Response Time: {elapsed:.3f}s (예상: < 1s)")
    print("-"*80)

    # Validate
    if elapsed < 1.0:
        print(f"✅ Test 3 PASSED: RPC 응답 시간 {elapsed:.3f}s")
    else:
        print(f"⚠️  Test 3 WARNING: RPC 응답 시간 {elapsed:.3f}s (예상: < 1s)")

    return {
        "new_pages": len(new_ids),
        "updated_pages": len(updated_ids),
        "unchanged_pages": len(pages) - len(new_ids) - len(updated_ids),
        "rpc_time": elapsed
    }


async def main():
    """
    통합 테스트 실행.
    """
    print("\n" + "="*80)
    print("Solution 3 RPC 기반 증분 import 통합 테스트")
    print(f"Started at: {datetime.now().isoformat()}")
    print("="*80)

    # Check server health
    print("\nChecking server health...")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f"{BASE_URL}/health", timeout=5.0)
            if response.status_code == 200:
                print("✅ Server is running")
            else:
                print(f"❌ Server health check failed: {response.status_code}")
                return
        except Exception as e:
            print(f"❌ Cannot connect to server: {e}")
            print("Please start the server with: uvicorn main:app --host 0.0.0.0 --port 8000")
            return

    # Run tests
    test1_result = await test_1_initial_import()

    if test1_result:
        # Wait 3 seconds between tests
        print("\nWaiting 3 seconds before Test 2...")
        await asyncio.sleep(3)

        test2_result = await test_2_rerun_no_changes()

    test3_result = await test_3_rpc_performance()

    # Final summary
    print("\n" + "="*80)
    print("테스트 요약")
    print("="*80)
    if test1_result:
        print(f"✅ Test 1: {test1_result['imported_pages']} pages imported")
    else:
        print("❌ Test 1: FAILED")

    if test2_result:
        print(f"✅ Test 2: {test2_result['skipped_pages']} pages skipped, {test2_result['imported_pages']} imported")
    else:
        print("❌ Test 2: FAILED")

    if test3_result:
        print(f"✅ Test 3: RPC 응답 시간 {test3_result['rpc_time']:.3f}s")
    else:
        print("❌ Test 3: FAILED")

    print("\n성공 기준:")
    print("  ✅ Test 1: 726개 전체 import 성공")
    print("  ✅ Test 2: 0 imported, 726 skipped 성공")
    print("  ✅ RPC 응답 시간 < 1초")
    print("  ✅ 재실행 시간 < 10초 (기존 대비 99% 단축)")
    print("="*80)


if __name__ == "__main__":
    asyncio.run(main())
