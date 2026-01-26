"""
Integration tests for hybrid strategy pipeline endpoints.

하이브리드 C 전략 - 파이프라인 엔드포인트 통합 테스트
"""

import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, MagicMock, patch

from main import app
from schemas.zk import PairScore, PairScoringResult


class TestCollectCandidatesEndpoint:
    """POST /pipeline/collect-candidates 통합 테스트"""

    @pytest.mark.asyncio
    async def test_collect_candidates_success(self):
        """후보 수집 성공: 30,000개 → DB 저장"""
        # Mock find_candidate_pairs
        mock_candidates = [
            {
                'thought_a_id': i * 2,
                'thought_b_id': i * 2 + 1,
                'similarity_score': 0.2,
                'raw_note_id_a': f'note-{i}',
                'raw_note_id_b': f'note-{i+1}'
            }
            for i in range(1, 101)
        ]

        # Mock insert_pair_candidates_batch
        from schemas.zk import PairCandidateBatch
        mock_batch_result = PairCandidateBatch(
            inserted_count=95,
            duplicate_count=5,
            error_count=0
        )

        with patch('services.supabase_service.SupabaseService') as mock_supabase:
            mock_service = MagicMock()
            mock_service._ensure_initialized = AsyncMock()
            mock_service.find_candidate_pairs = AsyncMock(return_value=mock_candidates)
            mock_service.insert_pair_candidates_batch = AsyncMock(return_value=mock_batch_result)
            mock_supabase.return_value = mock_service

            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    "/pipeline/collect-candidates",
                    params={'min_similarity': 0.05, 'max_similarity': 0.35, 'top_k': 20}
                )

            assert response.status_code == 200
            data = response.json()
            assert data['success'] is True
            assert data['total_candidates'] == 100
            assert data['inserted'] == 95
            assert data['duplicates'] == 5

    @pytest.mark.asyncio
    async def test_collect_candidates_empty(self):
        """후보 없음: success=True, total=0"""
        with patch('services.supabase_service.SupabaseService') as mock_supabase:
            mock_service = MagicMock()
            mock_service._ensure_initialized = AsyncMock()
            mock_service.find_candidate_pairs = AsyncMock(return_value=[])
            mock_supabase.return_value = mock_service

            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    "/pipeline/collect-candidates",
                    params={'top_k': 20}
                )

            assert response.status_code == 200
            data = response.json()
            assert data['success'] is True
            assert data['total_candidates'] == 0

    @pytest.mark.asyncio
    async def test_collect_candidates_invalid_similarity_range(self):
        """min_similarity >= max_similarity: 400 에러"""
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/pipeline/collect-candidates",
                params={'min_similarity': 0.5, 'max_similarity': 0.3}
            )

        assert response.status_code == 400
        assert 'min_similarity must be less than max_similarity' in response.json()['detail']


class TestSampleInitialEndpoint:
    """POST /pipeline/sample-initial 통합 테스트"""

    @pytest.mark.asyncio
    async def test_sample_initial_success(self):
        """초기 샘플링 성공: 100개 평가"""
        # Mock get_pending_candidates
        mock_pending = [
            {
                'id': i,
                'thought_a_id': i * 2,
                'thought_b_id': i * 2 + 1,
                'thought_a_claim': f'Claim A {i}',
                'thought_b_claim': f'Claim B {i}',
                'similarity': 0.2,
                'raw_note_id_a': f'note-{i}',
                'raw_note_id_b': f'note-{i+1}'
            }
            for i in range(1, 201)
        ]

        with patch('services.supabase_service.SupabaseService') as mock_supabase, \
             patch('services.ai_service.AIService') as mock_ai:

            # Supabase mock
            mock_supabase_service = MagicMock()
            mock_supabase_service._ensure_initialized = AsyncMock()
            mock_supabase_service.get_pending_candidates = AsyncMock(return_value=mock_pending)
            mock_supabase_service.update_candidate_score = AsyncMock()
            mock_supabase_service.move_to_thought_pairs = AsyncMock(return_value=45)
            mock_supabase.return_value = mock_supabase_service

            # AI service mock
            mock_ai_service = MagicMock()
            mock_ai_service.score_pairs = AsyncMock(return_value=PairScoringResult(
                pair_scores=[
                    PairScore(
                        thought_a_id=i * 2,
                        thought_b_id=i * 2 + 1,
                        logical_expansion_score=70 if i <= 45 else 50,
                        connection_reason=f'Connection {i}'
                    )
                    for i in range(1, 101)
                ]
            ))
            mock_ai.return_value = mock_ai_service

            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    "/pipeline/sample-initial",
                    params={'sample_size': 100}
                )

            assert response.status_code == 200
            data = response.json()
            assert data['success'] is True
            assert data['sampled'] == 100
            assert data['evaluated'] > 0

    @pytest.mark.asyncio
    async def test_sample_initial_no_pending(self):
        """pending 없음: 에러 메시지"""
        with patch('services.supabase_service.SupabaseService') as mock_supabase:
            mock_service = MagicMock()
            mock_service._ensure_initialized = AsyncMock()
            mock_service.get_pending_candidates = AsyncMock(return_value=[])
            mock_supabase.return_value = mock_service

            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    "/pipeline/sample-initial",
                    params={'sample_size': 100}
                )

            assert response.status_code == 200
            data = response.json()
            assert data['success'] is False
            assert 'No pending candidates' in data['errors'][0]


class TestScoreCandidatesEndpoint:
    """POST /pipeline/score-candidates 통합 테스트"""

    @pytest.mark.asyncio
    async def test_score_candidates_background(self):
        """백그라운드 실행: 즉시 응답 반환"""
        with patch('services.supabase_service.SupabaseService') as mock_supabase, \
             patch('services.ai_service.AIService') as mock_ai:

            mock_supabase_service = MagicMock()
            mock_supabase_service._ensure_initialized = AsyncMock()
            mock_supabase.return_value = mock_supabase_service

            mock_ai_service = MagicMock()
            mock_ai.return_value = mock_ai_service

            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    "/pipeline/score-candidates",
                    params={'max_candidates': 100}
                )

            assert response.status_code == 200
            data = response.json()
            assert data['success'] is True
            assert 'Batch evaluation started' in data['message']

    @pytest.mark.asyncio
    async def test_score_candidates_custom_max(self):
        """커스텀 max_candidates 파라미터"""
        with patch('services.supabase_service.SupabaseService') as mock_supabase, \
             patch('services.ai_service.AIService') as mock_ai:

            mock_supabase_service = MagicMock()
            mock_supabase_service._ensure_initialized = AsyncMock()
            mock_supabase.return_value = mock_supabase_service

            mock_ai_service = MagicMock()
            mock_ai.return_value = mock_ai_service

            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    "/pipeline/score-candidates",
                    params={'max_candidates': 250}
                )

            assert response.status_code == 200
            data = response.json()
            assert '250 candidates' in data['message']


class TestRecommendedEssaysEndpoint:
    """GET /essays/recommended 통합 테스트"""

    @pytest.mark.asyncio
    async def test_get_recommended_essays_success(self):
        """추천 Essay 조회 성공"""
        mock_pairs = [
            {
                'id': i,
                'thought_a_id': i * 2,
                'thought_b_id': i * 2 + 1,
                'similarity_score': 0.2,
                'connection_reason': f'Connection {i}',
                'claude_score': 90 - i,
                'quality_tier': 'excellent',
                'diversity_score': 0.8,
                'final_score': 85.0
            }
            for i in range(1, 6)
        ]

        with patch('services.recommendation.RecommendationEngine') as mock_engine:
            mock_instance = MagicMock()
            mock_instance.get_recommended_pairs = AsyncMock(return_value=mock_pairs)
            mock_engine.return_value = mock_instance

            with patch('services.supabase_service.SupabaseService') as mock_supabase:
                mock_service = MagicMock()
                mock_service._ensure_initialized = AsyncMock()
                mock_supabase.return_value = mock_service

                async with AsyncClient(app=app, base_url="http://test") as client:
                    response = await client.get(
                        "/pipeline/essays/recommended",
                        params={'limit': 5}
                    )

                assert response.status_code == 200
                data = response.json()
                assert data['total'] == 5
                assert len(data['pairs']) == 5
                assert all('quality_tier' in p for p in data['pairs'])

    @pytest.mark.asyncio
    async def test_get_recommended_essays_quality_tier_filter(self):
        """quality_tier 필터링"""
        mock_pairs = [
            {
                'id': 1,
                'thought_a_id': 2,
                'thought_b_id': 3,
                'similarity_score': 0.2,
                'connection_reason': 'Test',
                'claude_score': 95,
                'quality_tier': 'excellent',
                'diversity_score': 0.9,
                'final_score': 90.0
            }
        ]

        with patch('services.recommendation.RecommendationEngine') as mock_engine:
            mock_instance = MagicMock()
            mock_instance.get_recommended_pairs = AsyncMock(return_value=mock_pairs)
            mock_engine.return_value = mock_instance

            with patch('services.supabase_service.SupabaseService') as mock_supabase:
                mock_service = MagicMock()
                mock_service._ensure_initialized = AsyncMock()
                mock_supabase.return_value = mock_service

                async with AsyncClient(app=app, base_url="http://test") as client:
                    response = await client.get(
                        "/pipeline/essays/recommended",
                        params={'limit': 10, 'quality_tiers': ['excellent']}
                    )

                assert response.status_code == 200
                data = response.json()
                assert all(p['quality_tier'] == 'excellent' for p in data['pairs'])


class TestHybridPipelineEndToEnd:
    """하이브리드 전략 전체 플로우 통합 테스트"""

    @pytest.mark.asyncio
    async def test_full_hybrid_flow(self):
        """collect → sample → score 순차 실행"""
        # 이 테스트는 전체 플로우를 시뮬레이션합니다.
        # 실제로는 백그라운드 태스크가 완료되기까지 기다려야 하므로,
        # 여기서는 각 엔드포인트가 정상적으로 호출되는지만 확인합니다.

        from schemas.zk import PairCandidateBatch

        with patch('services.supabase_service.SupabaseService') as mock_supabase, \
             patch('services.ai_service.AIService') as mock_ai:

            # Supabase mock
            mock_supabase_service = MagicMock()
            mock_supabase_service._ensure_initialized = AsyncMock()
            mock_supabase_service.find_candidate_pairs = AsyncMock(return_value=[
                {
                    'thought_a_id': i * 2,
                    'thought_b_id': i * 2 + 1,
                    'similarity_score': 0.2,
                    'raw_note_id_a': f'note-{i}',
                    'raw_note_id_b': f'note-{i+1}'
                }
                for i in range(1, 101)
            ])
            mock_supabase_service.insert_pair_candidates_batch = AsyncMock(
                return_value=PairCandidateBatch(
                    inserted_count=100,
                    duplicate_count=0,
                    error_count=0
                )
            )
            mock_supabase_service.get_pending_candidates = AsyncMock(return_value=[
                {
                    'id': i,
                    'thought_a_id': i * 2,
                    'thought_b_id': i * 2 + 1,
                    'thought_a_claim': f'Claim A {i}',
                    'thought_b_claim': f'Claim B {i}',
                    'similarity': 0.2,
                    'raw_note_id_a': f'note-{i}',
                    'raw_note_id_b': f'note-{i+1}'
                }
                for i in range(1, 101)
            ])
            mock_supabase_service.update_candidate_score = AsyncMock()
            mock_supabase_service.move_to_thought_pairs = AsyncMock(return_value=30)
            mock_supabase.return_value = mock_supabase_service

            # AI service mock
            mock_ai_service = MagicMock()
            mock_ai_service.score_pairs = AsyncMock(return_value=PairScoringResult(
                pair_scores=[
                    PairScore(
                        thought_a_id=i * 2,
                        thought_b_id=i * 2 + 1,
                        logical_expansion_score=70,
                        connection_reason=f'Connection {i}'
                    )
                    for i in range(1, 11)
                ]
            ))
            mock_ai.return_value = mock_ai_service

            async with AsyncClient(app=app, base_url="http://test") as client:
                # Step 1: collect-candidates
                response1 = await client.post("/pipeline/collect-candidates")
                assert response1.status_code == 200

                # Step 2: sample-initial
                response2 = await client.post("/pipeline/sample-initial")
                assert response2.status_code == 200

                # Step 3: score-candidates (백그라운드)
                response3 = await client.post("/pipeline/score-candidates")
                assert response3.status_code == 200
