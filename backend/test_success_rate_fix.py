#!/usr/bin/env python3
"""
Success rate 로직 수정 검증 테스트

목표: skipped 페이지를 성공으로 간주하여 "completed" 상태 확인
"""

import asyncio
import httpx
import time


BASE_URL = "http://localhost:8000"


async def poll_job_status(client: httpx.AsyncClient, job_id: str, max_wait: int = 120) -> dict:
    """Job 상태 polling"""
    start_time = time.time()
    last_progress = None

    while time.time() - start_time < max_wait:
        response = await client.get(f"{BASE_URL}/pipeline/import-status/{job_id}")

        if response.status_code != 200:
            print(f"❌ Status check failed: {response.status_code}")
            return None

        data = response.json()
        status = data["status"]
        progress = data["progress_percentage"]

        if last_progress != progress:
            print(f"Progress: {progress}%, Status: {status}, Imported: {data['imported_pages']}, Skipped: {data['skipped_pages']}")
            last_progress = progress

        if status in ["completed", "failed"]:
            return data

        await asyncio.sleep(2)

    print(f"⏱️  Timeout: Job did not complete within {max_wait}s")
    return None


async def main():
    print("="*80)
    print("Success Rate 로직 수정 검증 테스트")
    print("="*80)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Start import
        print("\nStarting import job...")
        response = await client.post(f"{BASE_URL}/pipeline/import-from-notion?page_size=100")

        if response.status_code != 200:
            print(f"❌ Failed to start job: {response.status_code}")
            return

        data = response.json()
        job_id = data["job_id"]
        print(f"✅ Job started: {job_id}")

        # Poll
        final_status = await poll_job_status(client, job_id, max_wait=120)

        if final_status is None:
            print("❌ Test FAILED: Job did not complete")
            return

        # Print results
        print("\n" + "="*80)
        print("TEST 결과:")
        print(f"  Status: {final_status['status']}")
        print(f"  Total Pages: {final_status['total_pages']}")
        print(f"  Imported: {final_status['imported_pages']}")
        print(f"  Skipped: {final_status['skipped_pages']}")
        print(f"  Failed: {len(final_status['failed_pages'])}")
        print(f"  Error Message: {final_status.get('error_message', 'None')}")
        print("="*80)

        # Validate
        if final_status['status'] == "completed":
            print("\n✅ SUCCESS: Status is 'completed'")
            print(f"✅ Success rate 계산 로직 수정 완료")
            print(f"✅ {final_status['skipped_pages']} skipped pages counted as success")
        else:
            print(f"\n❌ FAILED: Status is '{final_status['status']}'")
            print(f"Error: {final_status.get('error_message')}")


if __name__ == "__main__":
    asyncio.run(main())
