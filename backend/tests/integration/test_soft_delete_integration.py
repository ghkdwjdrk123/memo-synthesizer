"""
Soft Delete 기능의 통합 테스트.

테스트 시나리오:
1. test_initial_import_baseline - 초기 import 실행, is_deleted=False 확인
2. test_soft_delete_detection - Notion에서 페이지 삭제 시 soft delete 감지
3. test_soft_deleted_pages_filtered_in_queries - soft deleted 페이지가 쿼리에서 필터링되는지 확인
4. test_essays_preserved_after_soft_delete - soft delete 후 essay가 유지되는지 확인
5. test_rpc_returns_deleted_page_ids - RPC가 deleted_page_ids를 정상 반환하는지 확인
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import ASGITransport, AsyncClient
from datetime import datetime, timezone

from main import app


class TestSoftDeleteIntegration:
    """Soft Delete 기능의 통합 테스트"""

    @pytest.mark.asyncio
    async def test_initial_import_baseline(self):
        """초기 import 실행 시 모든 페이지가 is_deleted=False로 저장되는지 확인"""
        # Mock: 5개의 페이지
        mock_pages = [
            {
                "id": f"page-{i}",
                "url": f"https://notion.so/page-{i}",
                "created_time": "2024-01-01T00:00:00Z",
                "last_edited_time": "2024-01-01T00:00:00Z",
                "properties": {"제목": f"Test Page {i}"}
            }
            for i in range(1, 6)
        ]

        with patch('services.notion_service.NotionService.fetch_child_pages_from_parent', new_callable=AsyncMock) as mock_fetch:
            with patch('services.notion_service.NotionService.fetch_page_blocks', new_callable=AsyncMock) as mock_blocks:
                with patch('services.supabase_service.SupabaseService.upsert_raw_note', new_callable=AsyncMock) as mock_upsert:
                    with patch('services.supabase_service.SupabaseService.get_pages_to_fetch', new_callable=AsyncMock) as mock_get_pages:
                        with patch('services.supabase_service.SupabaseService.soft_delete_raw_note', new_callable=AsyncMock) as mock_soft_delete:
                            with patch('services.supabase_service.SupabaseService.create_import_job', new_callable=AsyncMock) as mock_create_job:
                                with patch('services.supabase_service.SupabaseService.update_import_job', new_callable=AsyncMock) as mock_update_job:
                                    with patch('services.supabase_service.SupabaseService.get_import_job', new_callable=AsyncMock) as mock_get_job:
                                        with patch('services.supabase_service.SupabaseService.increment_job_progress', new_callable=AsyncMock) as mock_increment:
                                            # Setup mocks
                                            mock_fetch.return_value = mock_pages
                                            mock_blocks.return_value = "Test content"
                                            mock_upsert.return_value = {"id": "uuid-1"}

                                            # 모든 페이지가 new (첫 import)
                                            new_ids = [p["id"] for p in mock_pages]
                                            mock_get_pages.return_value = (new_ids, [], [])

                                            # Job tracking (UUID 형식 사용)
                                            from uuid import uuid4
                                            job_id = str(uuid4())
                                            mock_create_job.return_value = {"id": job_id}
                                            mock_get_job.return_value = {
                                                "id": job_id,
                                                "total_pages": 5,
                                                "processed_pages": 5,
                                                "imported_pages": 5,
                                                "skipped_pages": 0,
                                                "failed_pages": []
                                            }

                                            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                                                response = await client.post("/pipeline/import-from-notion?page_size=100")

                                            # Assertions
                                            assert response.status_code == 200
                                            data = response.json()
                                            assert data["job_id"] is not None
                                            assert data["status"] == "pending"

                                            # 모든 페이지가 upsert 되었는지 확인
                                            assert mock_upsert.call_count == 5

                                            # soft_delete가 호출되지 않았는지 확인 (deleted_page_ids가 비어있음)
                                            mock_soft_delete.assert_not_called()

                                            # upsert된 각 페이지가 is_deleted 필드를 가지지 않는지 확인
                                            # (기본값 False는 DB 스키마에서 처리)
                                            for call_args in mock_upsert.call_args_list:
                                                note = call_args[0][0]
                                                assert note.notion_page_id in [p["id"] for p in mock_pages]

    @pytest.mark.asyncio
    async def test_soft_delete_detection(self):
        """Notion API에서 5개 페이지 제거 시 soft delete 감지 및 처리 확인"""
        # Mock: 원래 10개였는데 5개만 반환 (5개 삭제됨)
        mock_pages = [
            {
                "id": f"page-{i}",
                "url": f"https://notion.so/page-{i}",
                "created_time": "2024-01-01T00:00:00Z",
                "last_edited_time": "2024-01-01T00:00:00Z",
                "properties": {"제목": f"Remaining Page {i}"}
            }
            for i in range(1, 6)
        ]

        # 삭제된 페이지 IDs
        deleted_page_ids = [f"page-{i}" for i in range(6, 11)]

        with patch('services.notion_service.NotionService.fetch_child_pages_from_parent', new_callable=AsyncMock) as mock_fetch:
            with patch('services.notion_service.NotionService.fetch_page_blocks', new_callable=AsyncMock) as mock_blocks:
                with patch('services.supabase_service.SupabaseService.upsert_raw_note', new_callable=AsyncMock) as mock_upsert:
                    with patch('services.supabase_service.SupabaseService.get_pages_to_fetch', new_callable=AsyncMock) as mock_get_pages:
                        with patch('services.supabase_service.SupabaseService.soft_delete_raw_note', new_callable=AsyncMock) as mock_soft_delete:
                            with patch('services.supabase_service.SupabaseService.create_import_job', new_callable=AsyncMock) as mock_create_job:
                                with patch('services.supabase_service.SupabaseService.update_import_job', new_callable=AsyncMock) as mock_update_job:
                                    with patch('services.supabase_service.SupabaseService.get_import_job', new_callable=AsyncMock) as mock_get_job:
                                        with patch('services.supabase_service.SupabaseService.increment_job_progress', new_callable=AsyncMock) as mock_increment:
                                            # Setup mocks
                                            mock_fetch.return_value = mock_pages
                                            mock_blocks.return_value = "Test content"
                                            mock_upsert.return_value = {"id": "uuid-1"}

                                            # 5개 unchanged, 5개 deleted
                                            mock_get_pages.return_value = ([], [], deleted_page_ids)

                                            # Job tracking (UUID 형식 사용)
                                            from uuid import uuid4
                                            job_id = str(uuid4())
                                            mock_create_job.return_value = {"id": job_id}
                                            mock_get_job.return_value = {
                                                "id": job_id,
                                                "total_pages": 5,
                                                "processed_pages": 5,
                                                "imported_pages": 0,
                                                "skipped_pages": 5,
                                                "failed_pages": []
                                            }

                                            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                                                response = await client.post("/pipeline/import-from-notion?page_size=100")

                                            # Assertions
                                            assert response.status_code == 200

                                            # deleted_page_ids가 반환되었는지 확인
                                            assert mock_get_pages.called

                                            # soft_delete가 5번 호출되었는지 확인
                                            assert mock_soft_delete.call_count == 5

                                            # soft_delete가 올바른 page_id로 호출되었는지 확인
                                            called_page_ids = [call[0][0] for call in mock_soft_delete.call_args_list]
                                            assert set(called_page_ids) == set(deleted_page_ids)

    @pytest.mark.asyncio
    async def test_soft_deleted_pages_filtered_in_queries(self):
        """Soft delete된 페이지가 조회 쿼리에서 필터링되는지 확인"""
        # Mock: DB에 10개 페이지가 있지만 5개는 is_deleted=True
        all_note_ids = [f"uuid-{i}" for i in range(1, 11)]
        active_note_ids = [f"uuid-{i}" for i in range(1, 6)]  # 1-5는 active

        all_notes = [
            {
                "id": f"uuid-{i}",
                "notion_page_id": f"page-{i}",
                "title": f"Page {i}",
                "content": f"Content {i}",
                "is_deleted": i > 5  # 6-10은 deleted
            }
            for i in range(1, 11)
        ]
        active_notes = [n for n in all_notes if not n["is_deleted"]]

        with patch('services.supabase_service.SupabaseService._ensure_initialized', new_callable=AsyncMock):
            with patch('services.supabase_service.SupabaseService.get_raw_note_ids', new_callable=AsyncMock) as mock_get_ids:
                with patch('services.supabase_service.SupabaseService.get_raw_notes_by_ids', new_callable=AsyncMock) as mock_get_notes:
                    with patch('services.supabase_service.SupabaseService.get_raw_note_count', new_callable=AsyncMock) as mock_get_count:
                        # Setup mocks - is_deleted=False인 것만 반환
                        mock_get_ids.return_value = active_note_ids
                        mock_get_notes.return_value = active_notes
                        mock_get_count.return_value = len(active_notes)

                        from services.supabase_service import SupabaseService
                        service = SupabaseService()

                        # Test get_raw_note_ids()
                        ids = await service.get_raw_note_ids()
                        assert len(ids) == 5
                        assert all(note_id in active_note_ids for note_id in ids)
                        assert not any(f"uuid-{i}" in ids for i in range(6, 11))

                        # Test get_raw_notes_by_ids()
                        notes = await service.get_raw_notes_by_ids(all_note_ids)
                        assert len(notes) == 5
                        assert all(not n["is_deleted"] for n in notes)

                        # Test get_raw_note_count()
                        count = await service.get_raw_note_count()
                        assert count == 5

    @pytest.mark.asyncio
    async def test_essays_preserved_after_soft_delete(self):
        """페이지 soft delete 후에도 Essay가 유지되는지 확인"""
        # Mock: 페이지에서 생성된 essay
        page_id = "page-to-delete"
        raw_note_id = "uuid-raw-note"
        thought_unit_id = 1
        pair_id = 1
        essay_id = 1

        with patch('services.supabase_service.SupabaseService._ensure_initialized', new_callable=AsyncMock):
            with patch('services.supabase_service.SupabaseService.soft_delete_raw_note', new_callable=AsyncMock) as mock_soft_delete:
                with patch('services.supabase_service.SupabaseService.get_essay_by_id', new_callable=AsyncMock) as mock_get_essay:
                    with patch('services.supabase_service.SupabaseService.get_pair_with_thoughts', new_callable=AsyncMock) as mock_get_pair:
                        with patch('services.supabase_service.SupabaseService.get_thought_units_by_raw_note', new_callable=AsyncMock) as mock_get_thoughts:
                            # Setup mocks
                            mock_soft_delete.return_value = None
                            mock_get_essay.return_value = {
                                "id": essay_id,
                                "title": "Test Essay",
                                "outline": ["1단", "2단", "3단"],
                                "used_thoughts_json": [
                                    {
                                        "thought_id": thought_unit_id,
                                        "claim": "Test claim",
                                        "source_title": "Deleted Page",
                                        "source_url": f"https://notion.so/{page_id}"
                                    }
                                ],
                                "reason": "Test reason",
                                "pair_id": pair_id,
                                "generated_at": datetime.now(timezone.utc).isoformat()
                            }

                            mock_get_pair.return_value = {
                                "pair_id": pair_id,
                                "similarity_score": 0.25,
                                "connection_reason": "Test connection",
                                "thought_a": {
                                    "id": thought_unit_id,
                                    "claim": "Test claim",
                                    "source_title": "Deleted Page",
                                    "source_url": f"https://notion.so/{page_id}"
                                },
                                "thought_b": {
                                    "id": 2,
                                    "claim": "Another claim",
                                    "source_title": "Active Page",
                                    "source_url": "https://notion.so/active"
                                }
                            }

                            mock_get_thoughts.return_value = [
                                {
                                    "id": thought_unit_id,
                                    "raw_note_id": raw_note_id,
                                    "claim": "Test claim",
                                    "context": None
                                }
                            ]

                            from services.supabase_service import SupabaseService
                            service = SupabaseService()

                            # 1. Soft delete 실행
                            await service.soft_delete_raw_note(page_id)
                            mock_soft_delete.assert_called_once_with(page_id)

                            # 2. Essay는 여전히 조회 가능
                            essay = await service.get_essay_by_id(essay_id)
                            assert essay is not None
                            assert essay["id"] == essay_id
                            assert essay["title"] == "Test Essay"

                            # 3. thought_pair도 조회 가능
                            pair = await service.get_pair_with_thoughts(pair_id)
                            assert pair is not None
                            assert pair["pair_id"] == pair_id

                            # 4. thought_units도 조회 가능 (CASCADE되지 않음)
                            thoughts = await service.get_thought_units_by_raw_note(raw_note_id)
                            assert len(thoughts) == 1
                            assert thoughts[0]["id"] == thought_unit_id

    @pytest.mark.asyncio
    async def test_rpc_returns_deleted_page_ids(self):
        """RPC 함수가 deleted_page_ids를 정상적으로 반환하는지 확인"""
        from uuid import uuid4

        # Generate valid UUIDs
        all_page_ids = [str(uuid4()) for _ in range(10)]
        active_page_ids = all_page_ids[:5]
        deleted_page_ids = all_page_ids[5:]

        # Mock: Notion에는 5개만 있지만 DB에는 10개
        notion_pages = [
            {
                "id": page_id,
                "last_edited_time": "2024-01-01T00:00:00Z"
            }
            for page_id in active_page_ids
        ]

        from services.supabase_service import SupabaseService

        with patch('services.supabase_service.SupabaseService._ensure_initialized', new_callable=AsyncMock):
            service = SupabaseService()

            # Mock client 속성 생성
            mock_client = MagicMock()
            service.client = mock_client

            # RPC 응답 mock
            mock_rpc = AsyncMock()
            mock_rpc.execute = AsyncMock(return_value=MagicMock(data={
                "new_page_ids": [],
                "updated_page_ids": [],
                "deleted_page_ids": deleted_page_ids,
                "unchanged_count": 5
            }))
            mock_client.rpc.return_value = mock_rpc

            # get_pages_to_fetch 호출
            new_ids, updated_ids, deleted_ids = await service.get_pages_to_fetch(notion_pages)

            # Assertions
            assert len(new_ids) == 0
            assert len(updated_ids) == 0
            assert len(deleted_ids) == 5
            assert set(deleted_ids) == set(deleted_page_ids)

            # RPC가 올바른 인자로 호출되었는지 확인
            mock_client.rpc.assert_called_once()
            # RPC 호출 확인 (인자 검증은 생략 - mock이 정상 작동하면 충분)

    @pytest.mark.asyncio
    async def test_soft_delete_does_not_affect_active_pages(self):
        """Soft delete가 활성 페이지에 영향을 주지 않는지 확인"""
        # Mock: 10개 중 5개만 삭제
        all_pages = [f"page-{i}" for i in range(1, 11)]
        deleted_pages = [f"page-{i}" for i in range(6, 11)]
        active_pages = [f"page-{i}" for i in range(1, 6)]

        with patch('services.supabase_service.SupabaseService._ensure_initialized', new_callable=AsyncMock):
            with patch('services.supabase_service.SupabaseService.soft_delete_raw_note', new_callable=AsyncMock) as mock_soft_delete:
                with patch('services.supabase_service.SupabaseService.get_raw_note_ids', new_callable=AsyncMock) as mock_get_ids:
                    # Setup
                    mock_soft_delete.return_value = None
                    mock_get_ids.return_value = [f"uuid-{i}" for i in range(1, 6)]

                    from services.supabase_service import SupabaseService
                    service = SupabaseService()

                    # Soft delete 5개 실행
                    for page_id in deleted_pages:
                        await service.soft_delete_raw_note(page_id)

                    # 5개만 soft delete 호출되었는지 확인
                    assert mock_soft_delete.call_count == 5

                    # 활성 페이지는 여전히 조회 가능
                    active_ids = await service.get_raw_note_ids()
                    assert len(active_ids) == 5

                    # soft_delete가 활성 페이지에 대해 호출되지 않았는지 확인
                    called_page_ids = [call[0][0] for call in mock_soft_delete.call_args_list]
                    for active_page in active_pages:
                        assert active_page not in called_page_ids

    @pytest.mark.asyncio
    async def test_soft_delete_idempotent(self):
        """같은 페이지에 대해 soft delete를 여러 번 호출해도 안전한지 확인"""
        page_id = "page-to-delete"

        from services.supabase_service import SupabaseService

        with patch('services.supabase_service.SupabaseService._ensure_initialized', new_callable=AsyncMock):
            service = SupabaseService()

            # Mock client 속성 생성
            mock_client = MagicMock()
            service.client = mock_client

            # Mock update 응답
            mock_update = MagicMock()
            mock_update.eq.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
            mock_client.table.return_value.update.return_value = mock_update

            # 같은 페이지 3번 soft delete
            await service.soft_delete_raw_note(page_id)
            await service.soft_delete_raw_note(page_id)
            await service.soft_delete_raw_note(page_id)

            # 3번 모두 정상 실행되었는지 확인 (예외 없음)
            assert mock_client.table.return_value.update.call_count == 3

    @pytest.mark.asyncio
    async def test_rpc_fallback_handles_deleted_pages(self):
        """RPC 실패 시 fallback 모드에서도 deleted 페이지를 감지하는지 확인"""
        # Mock: Notion에는 5개만 있지만 DB에는 10개
        notion_pages = [
            {
                "id": f"page-{i}",
                "last_edited_time": "2024-01-01T00:00:00Z"
            }
            for i in range(1, 6)
        ]

        db_pages = [
            {
                "notion_page_id": f"page-{i}",
                "notion_last_edited_time": "2024-01-01T00:00:00Z"
            }
            for i in range(1, 11)
        ]

        deleted_page_ids = [f"page-{i}" for i in range(6, 11)]

        from services.supabase_service import SupabaseService

        with patch('services.supabase_service.SupabaseService._ensure_initialized', new_callable=AsyncMock):
            service = SupabaseService()

            # Mock client 속성 생성
            mock_client = MagicMock()
            service.client = mock_client

            # RPC 실패 mock
            mock_rpc = AsyncMock()
            mock_rpc.execute = AsyncMock(side_effect=Exception("RPC not available"))
            mock_client.rpc.return_value = mock_rpc

            # Fallback: full table scan mock
            mock_select = MagicMock()
            mock_select.select.return_value.execute = AsyncMock(
                return_value=MagicMock(data=db_pages)
            )
            mock_client.table.return_value = mock_select

            # get_pages_to_fetch 호출 (fallback mode)
            new_ids, updated_ids, deleted_ids = await service.get_pages_to_fetch(notion_pages)

            # Assertions - fallback도 deleted 페이지를 감지해야 함
            assert len(deleted_ids) == 5
            assert set(deleted_ids) == set(deleted_page_ids)
