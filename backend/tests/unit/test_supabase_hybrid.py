"""
Unit tests for Supabase hybrid strategy methods.

하이브리드 C 전략 - Supabase 서비스 신규 메서드 테스트
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from services.supabase_service import SupabaseService
from schemas.zk import PairCandidateCreate, PairCandidateBatch


class TestSupabaseHybridMethods:
    """Supabase 하이브리드 전략 메서드 테스트"""

    @pytest.mark.asyncio
    async def test_insert_pair_candidates_batch_success(self):
        """배치 저장: 1000개 → inserted_count 확인"""
        service = SupabaseService()

        # Mock client
        mock_response = MagicMock()
        mock_response.data = [{'id': i} for i in range(1, 901)]  # 900개 저장됨 (100개 중복)

        with patch.object(service, '_ensure_initialized', AsyncMock()):
            service.client = MagicMock()
            service.client.table.return_value.upsert.return_value.execute = AsyncMock(
                return_value=mock_response
            )

            candidates = [
                PairCandidateCreate(
                    thought_a_id=i * 2,
                    thought_b_id=i * 2 + 1,
                    similarity=0.2,
                    raw_note_id_a=f'note-{i}',
                    raw_note_id_b=f'note-{i+1}'
                )
                for i in range(1000)
            ]

            result = await service.insert_pair_candidates_batch(candidates, batch_size=1000)

            assert isinstance(result, PairCandidateBatch)
            assert result.inserted_count == 900
            assert result.duplicate_count == 100
            assert result.error_count == 0

    @pytest.mark.asyncio
    async def test_insert_pair_candidates_batch_empty(self):
        """빈 리스트 입력: 경고 로그 + 빈 결과 반환"""
        service = SupabaseService()

        result = await service.insert_pair_candidates_batch([])

        assert result.inserted_count == 0
        assert result.duplicate_count == 0
        assert result.error_count == 0

    @pytest.mark.asyncio
    async def test_insert_pair_candidates_batch_partial_error(self):
        """부분 실패: 일부 배치 실패해도 나머지는 성공"""
        service = SupabaseService()

        call_count = 0

        async def mock_execute_with_failure():
            nonlocal call_count
            call_count += 1
            if call_count == 2:  # 2번째 배치 실패
                raise Exception("DB error")
            mock_resp = MagicMock()
            mock_resp.data = [{'id': i} for i in range(100)]
            return mock_resp

        with patch.object(service, '_ensure_initialized', AsyncMock()):
            service.client = MagicMock()
            service.client.table.return_value.upsert.return_value.execute = AsyncMock(
                side_effect=mock_execute_with_failure
            )

            candidates = [
                PairCandidateCreate(
                    thought_a_id=i,
                    thought_b_id=i + 1,
                    similarity=0.2,
                    raw_note_id_a='note-a',
                    raw_note_id_b='note-b'
                )
                for i in range(300)
            ]

            result = await service.insert_pair_candidates_batch(
                candidates,
                batch_size=100
            )

            # 3개 배치 중 2개 성공, 1개 실패
            assert result.inserted_count == 200
            assert result.error_count == 100

    @pytest.mark.asyncio
    async def test_get_pending_candidates_basic(self):
        """pending 조회: llm_status='pending' AND llm_attempts<3"""
        service = SupabaseService()

        # Mock pair_candidates 조회
        mock_candidates = [
            {
                'id': i,
                'thought_a_id': i * 2,
                'thought_b_id': i * 2 + 1,
                'similarity': 0.2,
                'raw_note_id_a': f'note-{i}',
                'raw_note_id_b': f'note-{i+1}'
            }
            for i in range(1, 11)
        ]

        # Mock thought_units 조회
        mock_thoughts = []
        for i in range(1, 11):
            mock_thoughts.append({'id': i * 2, 'claim': f'Claim A {i}'})
            mock_thoughts.append({'id': i * 2 + 1, 'claim': f'Claim B {i}'})

        with patch.object(service, '_ensure_initialized', AsyncMock()):
            service.client = MagicMock()

            # pair_candidates 조회 mock
            candidates_response = MagicMock()
            candidates_response.data = mock_candidates
            service.client.table.return_value.select.return_value.eq.return_value.lt.return_value.gte.return_value.lte.return_value.order.return_value.limit.return_value.execute = AsyncMock(
                return_value=candidates_response
            )

            # thought_units 조회 mock
            thoughts_response = MagicMock()
            thoughts_response.data = mock_thoughts
            service.client.table.return_value.select.return_value.in_.return_value.execute = AsyncMock(
                return_value=thoughts_response
            )

            result = await service.get_pending_candidates(limit=100)

            assert len(result) == 10
            assert all('thought_a_claim' in r for r in result)
            assert all('thought_b_claim' in r for r in result)

    @pytest.mark.asyncio
    async def test_get_pending_candidates_empty(self):
        """pending 없음: 빈 리스트 반환"""
        service = SupabaseService()

        with patch.object(service, '_ensure_initialized', AsyncMock()):
            service.client = MagicMock()
            mock_response = MagicMock()
            mock_response.data = []
            service.client.table.return_value.select.return_value.eq.return_value.lt.return_value.gte.return_value.lte.return_value.order.return_value.limit.return_value.execute = AsyncMock(
                return_value=mock_response
            )

            result = await service.get_pending_candidates(limit=100)

            assert result == []

    @pytest.mark.asyncio
    async def test_get_pending_candidates_missing_claim(self):
        """claim 누락된 후보는 스킵"""
        service = SupabaseService()

        # Mock pair_candidates
        mock_candidates = [
            {
                'id': 1,
                'thought_a_id': 10,
                'thought_b_id': 20,
                'similarity': 0.2,
                'raw_note_id_a': 'note-1',
                'raw_note_id_b': 'note-2'
            },
            {
                'id': 2,
                'thought_a_id': 30,
                'thought_b_id': 40,
                'similarity': 0.2,
                'raw_note_id_a': 'note-3',
                'raw_note_id_b': 'note-4'
            }
        ]

        # Mock thought_units (ID 30의 claim 누락)
        mock_thoughts = [
            {'id': 10, 'claim': 'Claim A'},
            {'id': 20, 'claim': 'Claim B'},
            # ID 30 누락
            {'id': 40, 'claim': 'Claim D'}
        ]

        with patch.object(service, '_ensure_initialized', AsyncMock()):
            service.client = MagicMock()

            candidates_response = MagicMock()
            candidates_response.data = mock_candidates
            service.client.table.return_value.select.return_value.eq.return_value.lt.return_value.gte.return_value.lte.return_value.order.return_value.limit.return_value.execute = AsyncMock(
                return_value=candidates_response
            )

            thoughts_response = MagicMock()
            thoughts_response.data = mock_thoughts
            service.client.table.return_value.select.return_value.in_.return_value.execute = AsyncMock(
                return_value=thoughts_response
            )

            result = await service.get_pending_candidates(limit=100)

            # ID 2는 스킵되어야 함
            assert len(result) == 1
            assert result[0]['id'] == 1

    @pytest.mark.asyncio
    async def test_update_candidate_score_success(self):
        """Claude 평가 결과 업데이트: llm_attempts 자동 증가"""
        service = SupabaseService()

        with patch.object(service, '_ensure_initialized', AsyncMock()):
            service.client = MagicMock()

            # Mock get (현재 attempts 조회)
            get_response = MagicMock()
            get_response.data = {'llm_attempts': 1}
            service.client.table.return_value.select.return_value.eq.return_value.single.return_value.execute = AsyncMock(
                return_value=get_response
            )

            # Mock update
            update_response = MagicMock()
            update_response.data = [{
                'id': 123,
                'llm_score': 85,
                'llm_status': 'completed',
                'llm_attempts': 2,
                'connection_reason': 'Test reason'
            }]
            service.client.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(
                return_value=update_response
            )

            result = await service.update_candidate_score(
                candidate_id=123,
                llm_score=85,
                connection_reason='Test reason'
            )

            assert result['llm_score'] == 85
            assert result['llm_status'] == 'completed'
            assert result['llm_attempts'] == 2

    @pytest.mark.asyncio
    async def test_update_candidate_score_not_found(self):
        """존재하지 않는 candidate_id: Exception 발생"""
        service = SupabaseService()

        with patch.object(service, '_ensure_initialized', AsyncMock()):
            service.client = MagicMock()

            # Mock get (빈 결과)
            get_response = MagicMock()
            get_response.data = None
            service.client.table.return_value.select.return_value.eq.return_value.single.return_value.execute = AsyncMock(
                return_value=get_response
            )

            with pytest.raises(Exception, match="Candidate .* not found"):
                await service.update_candidate_score(
                    candidate_id=999,
                    llm_score=50,
                    connection_reason='Test'
                )

    @pytest.mark.asyncio
    async def test_move_to_thought_pairs_success(self):
        """고득점 이동: quality_tier 계산 확인"""
        service = SupabaseService()

        # Mock pair_candidates 조회 (3개)
        mock_candidates = [
            {
                'id': 1,
                'thought_a_id': 10,
                'thought_b_id': 20,
                'similarity': 0.2,
                'llm_score': 95,  # excellent
                'llm_status': 'completed',
                'connection_reason': 'Excellent connection'
            },
            {
                'id': 2,
                'thought_a_id': 30,
                'thought_b_id': 40,
                'similarity': 0.3,
                'llm_score': 88,  # premium
                'llm_status': 'completed',
                'connection_reason': 'Premium connection'
            },
            {
                'id': 3,
                'thought_a_id': 50,
                'thought_b_id': 60,
                'similarity': 0.25,
                'llm_score': 70,  # standard
                'llm_status': 'completed',
                'connection_reason': 'Standard connection'
            }
        ]

        with patch.object(service, '_ensure_initialized', AsyncMock()):
            service.client = MagicMock()

            # Mock select candidates
            select_response = MagicMock()
            select_response.data = mock_candidates
            service.client.table.return_value.select.return_value.in_.return_value.gte.return_value.eq.return_value.execute = AsyncMock(
                return_value=select_response
            )

            # Mock upsert to thought_pairs
            upsert_response = MagicMock()
            upsert_response.data = [
                {'id': 101, 'thought_a_id': 10, 'thought_b_id': 20},
                {'id': 102, 'thought_a_id': 30, 'thought_b_id': 40},
                {'id': 103, 'thought_a_id': 50, 'thought_b_id': 60}
            ]
            service.client.table.return_value.upsert.return_value.execute = AsyncMock(
                return_value=upsert_response
            )

            result = await service.move_to_thought_pairs(
                candidate_ids=[1, 2, 3],
                min_score=65
            )

            assert result == 3

            # upsert 호출 검증
            call_args = service.client.table.return_value.upsert.call_args
            pairs_data = call_args[0][0]

            # quality_tier 검증
            assert pairs_data[0]['quality_tier'] == 'excellent'
            assert pairs_data[1]['quality_tier'] == 'premium'
            assert pairs_data[2]['quality_tier'] == 'standard'

    @pytest.mark.asyncio
    async def test_move_to_thought_pairs_empty(self):
        """빈 candidate_ids: 0 반환"""
        service = SupabaseService()

        result = await service.move_to_thought_pairs(
            candidate_ids=[],
            min_score=65
        )

        assert result == 0

    @pytest.mark.asyncio
    async def test_move_to_thought_pairs_min_score_filter(self):
        """min_score 필터링: 기준 미달은 제외"""
        service = SupabaseService()

        # Mock candidates (1개는 점수 낮음)
        mock_candidates = [
            {
                'id': 1,
                'thought_a_id': 10,
                'thought_b_id': 20,
                'similarity': 0.2,
                'llm_score': 70,
                'llm_status': 'completed',
                'connection_reason': 'Good'
            }
            # ID 2는 점수 60점으로 필터링됨
        ]

        with patch.object(service, '_ensure_initialized', AsyncMock()):
            service.client = MagicMock()

            select_response = MagicMock()
            select_response.data = mock_candidates
            service.client.table.return_value.select.return_value.in_.return_value.gte.return_value.eq.return_value.execute = AsyncMock(
                return_value=select_response
            )

            upsert_response = MagicMock()
            upsert_response.data = [{'id': 101}]
            service.client.table.return_value.upsert.return_value.execute = AsyncMock(
                return_value=upsert_response
            )

            result = await service.move_to_thought_pairs(
                candidate_ids=[1, 2],
                min_score=65
            )

            # 1개만 이동됨
            assert result == 1

    @pytest.mark.asyncio
    async def test_move_to_thought_pairs_quality_tier_boundaries(self):
        """quality_tier 경계값 정확성"""
        service = SupabaseService()

        # Mock candidates (경계값 테스트)
        mock_candidates = [
            {'id': 1, 'thought_a_id': 1, 'thought_b_id': 2, 'similarity': 0.2,
             'llm_score': 65, 'llm_status': 'completed', 'connection_reason': 'A'},  # standard (lower bound)
            {'id': 2, 'thought_a_id': 3, 'thought_b_id': 4, 'similarity': 0.2,
             'llm_score': 84, 'llm_status': 'completed', 'connection_reason': 'B'},  # standard (upper bound)
            {'id': 3, 'thought_a_id': 5, 'thought_b_id': 6, 'similarity': 0.2,
             'llm_score': 85, 'llm_status': 'completed', 'connection_reason': 'C'},  # premium (lower bound)
            {'id': 4, 'thought_a_id': 7, 'thought_b_id': 8, 'similarity': 0.2,
             'llm_score': 94, 'llm_status': 'completed', 'connection_reason': 'D'},  # premium (upper bound)
            {'id': 5, 'thought_a_id': 9, 'thought_b_id': 10, 'similarity': 0.2,
             'llm_score': 95, 'llm_status': 'completed', 'connection_reason': 'E'},  # excellent (lower bound)
        ]

        with patch.object(service, '_ensure_initialized', AsyncMock()):
            service.client = MagicMock()

            select_response = MagicMock()
            select_response.data = mock_candidates
            service.client.table.return_value.select.return_value.in_.return_value.gte.return_value.eq.return_value.execute = AsyncMock(
                return_value=select_response
            )

            upsert_response = MagicMock()
            upsert_response.data = [{'id': i} for i in range(5)]
            service.client.table.return_value.upsert.return_value.execute = AsyncMock(
                return_value=upsert_response
            )

            await service.move_to_thought_pairs(
                candidate_ids=[1, 2, 3, 4, 5],
                min_score=65
            )

            call_args = service.client.table.return_value.upsert.call_args
            pairs_data = call_args[0][0]

            assert pairs_data[0]['quality_tier'] == 'standard'
            assert pairs_data[1]['quality_tier'] == 'standard'
            assert pairs_data[2]['quality_tier'] == 'premium'
            assert pairs_data[3]['quality_tier'] == 'premium'
            assert pairs_data[4]['quality_tier'] == 'excellent'
