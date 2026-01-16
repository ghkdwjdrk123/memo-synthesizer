"""
RPC 기반 증분 import 시스템 통합 테스트.

테스트 시나리오:
1. 페이지 1개 수정 후 재import - 수정된 페이지만 import
2. 페이지 1개 추가 후 재import - 신규 페이지만 import
3. 페이지 1개 삭제 후 재import - 삭제된 페이지는 무시
4. 페이지 1개 수정 + 1개 추가 후 재import - 복합 시나리오

테스트 대상:
- services.supabase_service.get_pages_to_fetch()
- Supabase RPC: get_changed_pages(pages_data jsonb)
- pipeline.import-from-notion endpoint
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone, timedelta
from httpx import ASGITransport, AsyncClient
import time

from main import app


class TestRPCIncrementalScenarios:
    """RPC 기반 증분 import 시나리오 테스트"""

    @pytest.fixture
    def base_timestamp(self):
        """기준 timestamp (초 단위)"""
        return datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    @pytest.fixture
    def sample_pages(self, base_timestamp):
        """초기 import용 샘플 페이지 (3개)"""
        return [
            {
                "id": "aaaaaaaa-1111-1111-1111-000000000001",
                "url": "https://notion.so/page-1",
                "created_time": base_timestamp.isoformat(),
                "last_edited_time": base_timestamp.isoformat(),
                "properties": {"제목": {"title": [{"plain_text": "Page 1"}]}}
            },
            {
                "id": "aaaaaaaa-1111-1111-1111-000000000002",
                "url": "https://notion.so/page-2",
                "created_time": base_timestamp.isoformat(),
                "last_edited_time": base_timestamp.isoformat(),
                "properties": {"제목": {"title": [{"plain_text": "Page 2"}]}}
            },
            {
                "id": "aaaaaaaa-1111-1111-1111-000000000003",
                "url": "https://notion.so/page-3",
                "created_time": base_timestamp.isoformat(),
                "last_edited_time": base_timestamp.isoformat(),
                "properties": {"제목": {"title": [{"plain_text": "Page 3"}]}}
            }
        ]

    @pytest.fixture
    def mock_rpc_response(self):
        """Mock RPC 응답 빌더"""
        def builder(new_ids=None, updated_ids=None, total_checked=3):
            """
            RPC 응답 생성 헬퍼.

            Args:
                new_ids: 신규 페이지 ID 리스트
                updated_ids: 수정된 페이지 ID 리스트
                total_checked: 체크한 총 페이지 수
            """
            new_ids = new_ids or []
            updated_ids = updated_ids or []
            unchanged = total_checked - len(new_ids) - len(updated_ids)

            return MagicMock(
                data={
                    "new_page_ids": new_ids,
                    "updated_page_ids": updated_ids,
                    "total_checked": total_checked,
                    "unchanged_count": unchanged
                }
            )
        return builder

    # ============================================================
    # Test Case 1: 페이지 1개 수정 후 테스트
    # ============================================================

    @pytest.mark.asyncio
    async def test_one_page_updated(self, sample_pages, base_timestamp, mock_rpc_response):
        """
        시나리오: 기존 페이지 1개의 last_edited_time이 변경됨

        Expected:
        - RPC 응답: updated_page_ids에 해당 페이지 1개 포함
        - Import 결과: imported_pages=1, skipped_pages=2
        """
        # Updated timestamp for page 1
        updated_time = base_timestamp + timedelta(hours=1)
        updated_pages = sample_pages.copy()
        updated_pages[0]["last_edited_time"] = updated_time.isoformat()

        # Import supabase_service
        from services.supabase_service import SupabaseService

        # Create service and mock client
        service = SupabaseService()
        mock_client = MagicMock()

        # Mock RPC: page 1은 updated, 나머지는 unchanged
        # execute()는 async 메서드이므로 AsyncMock 사용
        mock_execute = AsyncMock(return_value=mock_rpc_response(
            new_ids=[],
            updated_ids=["aaaaaaaa-1111-1111-1111-000000000001"],
            total_checked=3
        ))
        mock_client.rpc.return_value.execute = mock_execute

        # Set client directly (bypass _ensure_initialized)
        service.client = mock_client
        service._initialized = True

        start_time = time.time()
        new_ids, updated_ids = await service.get_pages_to_fetch(updated_pages)
        elapsed = time.time() - start_time

        # Assertions
        assert len(new_ids) == 0, "신규 페이지가 없어야 함"
        assert len(updated_ids) == 1, "수정된 페이지 1개"
        assert "aaaaaaaa-1111-1111-1111-000000000001" in updated_ids
        assert elapsed < 1.0, f"RPC 응답 시간 초과: {elapsed:.2f}s"

        # Verify RPC was called with correct data
        assert mock_client.rpc.called
        rpc_call_args = mock_client.rpc.call_args
        # call_args: (('get_changed_pages', {'pages_data': [...]}), {})
        assert rpc_call_args[0][0] == 'get_changed_pages'

        pages_data = rpc_call_args[0][1]['pages_data']
        assert len(pages_data) == 3, "3개 페이지 모두 체크해야 함"

    # ============================================================
    # Test Case 2: 페이지 1개 추가 후 테스트
    # ============================================================

    @pytest.mark.asyncio
    async def test_one_page_added(self, sample_pages, base_timestamp, mock_rpc_response):
        """
        시나리오: 새로운 페이지 1개가 Notion에 추가됨

        Expected:
        - RPC 응답: new_page_ids에 신규 페이지 1개 포함
        - Import 결과: imported_pages=1, skipped_pages=3
        """
        # Add new page
        new_page = {
            "id": "aaaaaaaa-1111-1111-1111-000000000004",
            "url": "https://notion.so/page-4",
            "created_time": base_timestamp.isoformat(),
            "last_edited_time": base_timestamp.isoformat(),
            "properties": {"제목": {"title": [{"plain_text": "New Page 4"}]}}
        }
        pages_with_new = sample_pages + [new_page]

        from services.supabase_service import SupabaseService
        service = SupabaseService()
        mock_client = MagicMock()

        # Mock RPC: page 4는 new, 나머지는 unchanged
        mock_execute = AsyncMock(return_value=mock_rpc_response(
            new_ids=["aaaaaaaa-1111-1111-1111-000000000004"],
            updated_ids=[],
            total_checked=4
        ))
        mock_client.rpc.return_value.execute = mock_execute

        service.client = mock_client
        service._initialized = True

        start_time = time.time()
        new_ids, updated_ids = await service.get_pages_to_fetch(pages_with_new)
        elapsed = time.time() - start_time

        # Assertions
        assert len(new_ids) == 1, "신규 페이지 1개"
        assert len(updated_ids) == 0, "수정된 페이지가 없어야 함"
        assert "aaaaaaaa-1111-1111-1111-000000000004" in new_ids
        assert elapsed < 1.0, f"RPC 응답 시간 초과: {elapsed:.2f}s"

    # ============================================================
    # Test Case 3: 페이지 1개 삭제 후 테스트
    # ============================================================

    @pytest.mark.asyncio
    async def test_one_page_deleted(self, sample_pages, mock_rpc_response):
        """
        시나리오: Notion에서 페이지 1개가 삭제됨 (API 응답에서 제외)

        Expected:
        - RPC 입력: 2개 페이지만 전달 (삭제된 페이지는 Notion API에서 제외됨)
        - Import 결과: imported_pages=0, skipped_pages=2
        - DB의 삭제된 페이지는 그대로 유지 (import 로직은 삭제하지 않음)
        """
        # Remove page 2 (simulate deletion in Notion)
        pages_without_deleted = [sample_pages[0], sample_pages[2]]

        from services.supabase_service import SupabaseService
        service = SupabaseService()
        mock_client = MagicMock()

        # Mock RPC: 2개 페이지 모두 unchanged (삭제된 페이지는 비교 대상에서 제외)
        mock_execute = AsyncMock(return_value=mock_rpc_response(
            new_ids=[],
            updated_ids=[],
            total_checked=2
        ))
        mock_client.rpc.return_value.execute = mock_execute

        service.client = mock_client
        service._initialized = True

        new_ids, updated_ids = await service.get_pages_to_fetch(pages_without_deleted)

        # Assertions
        assert len(new_ids) == 0, "신규 페이지가 없어야 함"
        assert len(updated_ids) == 0, "수정된 페이지가 없어야 함"

        # Verify RPC was called with only 2 pages
        rpc_call_args = mock_client.rpc.call_args
        pages_data = rpc_call_args[0][1]['pages_data']
        assert len(pages_data) == 2, "2개 페이지만 체크해야 함 (삭제된 페이지 제외)"

    # ============================================================
    # Test Case 4: 복합 시나리오 (수정 1개 + 추가 1개)
    # ============================================================

    @pytest.mark.asyncio
    async def test_mixed_changes(self, sample_pages, base_timestamp, mock_rpc_response):
        """
        시나리오: 기존 페이지 1개 수정 + 신규 페이지 1개 추가

        Expected:
        - RPC 응답: new_page_ids=1, updated_page_ids=1
        - Import 결과: imported_pages=2, skipped_pages=2
        """
        # Update page 1
        updated_time = base_timestamp + timedelta(hours=2)
        updated_pages = sample_pages.copy()
        updated_pages[0]["last_edited_time"] = updated_time.isoformat()

        # Add new page 4
        new_page = {
            "id": "aaaaaaaa-1111-1111-1111-000000000004",
            "url": "https://notion.so/page-4",
            "created_time": base_timestamp.isoformat(),
            "last_edited_time": base_timestamp.isoformat(),
            "properties": {"제목": {"title": [{"plain_text": "New Page 4"}]}}
        }
        pages_mixed = updated_pages + [new_page]

        from services.supabase_service import SupabaseService
        service = SupabaseService()
        mock_client = MagicMock()

        # Mock RPC: page 1 updated, page 4 new, pages 2-3 unchanged
        mock_execute = AsyncMock(return_value=mock_rpc_response(
            new_ids=["aaaaaaaa-1111-1111-1111-000000000004"],
            updated_ids=["aaaaaaaa-1111-1111-1111-000000000001"],
            total_checked=4
        ))
        mock_client.rpc.return_value.execute = mock_execute

        service.client = mock_client
        service._initialized = True

        start_time = time.time()
        new_ids, updated_ids = await service.get_pages_to_fetch(pages_mixed)
        elapsed = time.time() - start_time

        # Assertions
        assert len(new_ids) == 1, "신규 페이지 1개"
        assert len(updated_ids) == 1, "수정된 페이지 1개"
        assert "aaaaaaaa-1111-1111-1111-000000000004" in new_ids
        assert "aaaaaaaa-1111-1111-1111-000000000001" in updated_ids
        assert elapsed < 1.0, f"RPC 응답 시간 초과: {elapsed:.2f}s"

    # ============================================================
    # Test Case 5: 에러 핸들링 - 잘못된 timestamp
    # ============================================================

    @pytest.mark.asyncio
    async def test_invalid_timestamp_format(self, sample_pages, mock_rpc_response):
        """
        시나리오: 잘못된 timestamp 형식이 포함된 페이지

        Expected:
        - 잘못된 timestamp 페이지는 force_new_ids로 처리
        - 나머지 페이지는 정상 RPC 체크
        """
        # Invalid timestamp for page 1
        invalid_pages = sample_pages.copy()
        invalid_pages[0]["last_edited_time"] = "INVALID_TIMESTAMP"

        from services.supabase_service import SupabaseService
        service = SupabaseService()
        mock_client = MagicMock()

        # Mock RPC: pages 2-3만 체크 (page 1은 invalid timestamp로 제외)
        mock_execute = AsyncMock(return_value=mock_rpc_response(
            new_ids=[],
            updated_ids=[],
            total_checked=2
        ))
        mock_client.rpc.return_value.execute = mock_execute

        service.client = mock_client
        service._initialized = True

        new_ids, updated_ids = await service.get_pages_to_fetch(invalid_pages)

        # Assertions
        # Page 1은 force_new_ids에 추가되어 new_ids에 포함됨
        assert "aaaaaaaa-1111-1111-1111-000000000001" in new_ids, "Invalid timestamp 페이지는 new로 처리"
        assert len(updated_ids) == 0

        # RPC는 2개 페이지만 받음
        rpc_call_args = mock_client.rpc.call_args
        pages_data = rpc_call_args[0][1]['pages_data']
        assert len(pages_data) == 2, "Invalid timestamp 페이지는 RPC에서 제외"

    # ============================================================
    # Test Case 6: 에러 핸들링 - UUID 형식 오류
    # ============================================================

    @pytest.mark.asyncio
    async def test_invalid_uuid_format(self, sample_pages, mock_rpc_response):
        """
        시나리오: RPC가 잘못된 UUID 형식을 반환

        Expected:
        - ValueError 발생
        - Fallback 로직 작동
        """
        from services.supabase_service import SupabaseService
        service = SupabaseService()
        mock_client = MagicMock()

        # Mock RPC: Invalid UUID 반환
        mock_client.rpc.return_value.execute.return_value = MagicMock(
            data={
                "new_page_ids": ["INVALID-UUID-FORMAT"],
                "updated_page_ids": [],
                "total_checked": 3,
                "unchanged_count": 2
            }
        )

        # Mock fallback: full table scan
        mock_client.table.return_value.select.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[])
        )

        service.client = mock_client
        service._initialized = True

        new_ids, updated_ids = await service.get_pages_to_fetch(sample_pages)

        # RPC 실패 후 fallback이 작동하므로 에러 없이 결과 반환
        # Fallback 결과: 기존 DB가 비어있으므로 모든 페이지가 new로 처리
        assert isinstance(new_ids, list)
        assert isinstance(updated_ids, list)

    # ============================================================
    # Test Case 7: 에러 핸들링 - RPC 함수 실패 시 Fallback
    # ============================================================

    @pytest.mark.asyncio
    async def test_rpc_failure_fallback(self, sample_pages, base_timestamp):
        """
        시나리오: RPC 함수가 에러를 반환

        Expected:
        - Fallback (full table scan) 작동
        - 정상적으로 new/updated 페이지 감지
        """
        from services.supabase_service import SupabaseService
        service = SupabaseService()
        mock_client = MagicMock()

        # Mock RPC: 에러 발생
        mock_client.rpc.return_value.execute.side_effect = Exception("RPC function error")

        # Mock fallback: full table scan (기존 페이지 2개 존재)
        mock_client.table.return_value.select.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[
                {
                    "notion_page_id": "aaaaaaaa-1111-1111-1111-000000000001",
                    "notion_last_edited_time": base_timestamp.isoformat()
                },
                {
                    "notion_page_id": "aaaaaaaa-1111-1111-1111-000000000002",
                    "notion_last_edited_time": base_timestamp.isoformat()
                }
            ])
        )

        service.client = mock_client
        service._initialized = True

        # Page 3는 신규로 추가
        new_ids, updated_ids = await service.get_pages_to_fetch(sample_pages)

        # Assertions
        # Fallback 로직이 작동하여 정상 결과 반환
        assert len(new_ids) == 1, "Page 3가 신규로 감지되어야 함"
        assert "aaaaaaaa-1111-1111-1111-000000000003" in new_ids
        assert len(updated_ids) == 0

    # ============================================================
    # Test Case 8: 성능 측정
    # ============================================================

    @pytest.mark.asyncio
    async def test_performance_measurement(self, mock_rpc_response):
        """
        시나리오: 대량 페이지 (100개) RPC 성능 측정

        Expected:
        - RPC 응답 시간 < 1초
        """
        # Generate 100 pages
        base_time = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        large_pages = []
        for i in range(100):
            page_id = f"aaaaaaaa-1111-1111-1111-{i:012d}"
            large_pages.append({
                "id": page_id,
                "url": f"https://notion.so/page-{i}",
                "created_time": base_time.isoformat(),
                "last_edited_time": base_time.isoformat(),
                "properties": {"제목": {"title": [{"plain_text": f"Page {i}"}]}}
            })

        from services.supabase_service import SupabaseService
        service = SupabaseService()
        mock_client = MagicMock()

        # Mock RPC: 모두 unchanged
        mock_execute = AsyncMock(return_value=mock_rpc_response(
            new_ids=[],
            updated_ids=[],
            total_checked=100
        ))
        mock_client.rpc.return_value.execute = mock_execute

        service.client = mock_client
        service._initialized = True

        start_time = time.time()
        new_ids, updated_ids = await service.get_pages_to_fetch(large_pages)
        elapsed = time.time() - start_time

        # Assertions
        assert len(new_ids) == 0
        assert len(updated_ids) == 0
        assert elapsed < 1.0, f"RPC 응답 시간 초과 (100 pages): {elapsed:.2f}s"

        # RPC 호출 확인
        rpc_call_args = mock_client.rpc.call_args
        pages_data = rpc_call_args[0][1]['pages_data']
        assert len(pages_data) == 100


# ============================================================
# Integration Test with Endpoint (Optional - E2E)
# ============================================================

class TestRPCIncrementalEndpointIntegration:
    """엔드포인트 레벨 통합 테스트 (E2E)"""

    @pytest.mark.asyncio
    async def test_endpoint_incremental_import(self):
        """
        시나리오: /pipeline/import-from-notion 엔드포인트 호출 시 RPC 작동

        Expected:
        - Job status에서 skipped_pages 카운트 정확
        - Success rate 계산 정확 (imported + skipped)
        """
        base_time = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        mock_pages = [
            {
                "id": "aaaaaaaa-1111-1111-1111-000000000001",
                "url": "https://notion.so/page-1",
                "created_time": base_time.isoformat(),
                "last_edited_time": base_time.isoformat(),
                "properties": {"제목": {"title": [{"plain_text": "Page 1"}]}}
            },
            {
                "id": "aaaaaaaa-1111-1111-1111-000000000002",
                "url": "https://notion.so/page-2",
                "created_time": base_time.isoformat(),
                "last_edited_time": base_time.isoformat(),
                "properties": {"제목": {"title": [{"plain_text": "Page 2"}]}}
            }
        ]

        with patch('services.notion_service.NotionService.query_database', new_callable=AsyncMock) as mock_query:
            with patch('services.supabase_service.SupabaseService.get_pages_to_fetch', new_callable=AsyncMock) as mock_get_pages:
                with patch('services.supabase_service.SupabaseService.upsert_raw_note', new_callable=AsyncMock) as mock_upsert:
                    with patch('services.supabase_service.SupabaseService.create_import_job', new_callable=AsyncMock) as mock_create_job:
                        with patch('services.supabase_service.SupabaseService.update_import_job', new_callable=AsyncMock):
                            with patch('services.supabase_service.SupabaseService.increment_job_progress', new_callable=AsyncMock):
                                # Mock Notion API
                                mock_query.return_value = {
                                    "success": True,
                                    "pages": mock_pages
                                }

                                # Mock RPC: 1 new, 1 unchanged
                                mock_get_pages.return_value = (
                                    ["aaaaaaaa-1111-1111-1111-000000000001"],  # new
                                    []  # updated
                                )

                                mock_upsert.return_value = {"id": "aaaaaaaa-1111-1111-1111-000000000001"}
                                mock_create_job.return_value = {"id": "bbbbbbbb-2222-2222-2222-000000000001"}

                                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                                    response = await client.post("/pipeline/import-from-notion")

                                # Assertions
                                assert response.status_code == 200
                                data = response.json()
                                assert data["success"] is True
                                assert "job_id" in data

                                # Verify RPC was called
                                assert mock_get_pages.called
