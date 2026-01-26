"""
Unit tests for BatchEvaluationWorker class.

하이브리드 C 전략 - 배치 평가 워커 테스트
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from services.batch_worker import BatchEvaluationWorker
from schemas.zk import PairScore, PairScoringResult


class TestBatchEvaluationWorker:
    """BatchEvaluationWorker 클래스 테스트"""

    @pytest.mark.asyncio
    async def test_init_default_params(self):
        """기본 파라미터로 워커 초기화"""
        supabase = MagicMock()
        ai = MagicMock()

        worker = BatchEvaluationWorker(
            supabase_service=supabase,
            ai_service=ai
        )

        assert worker.batch_size == 10
        assert worker.min_score_threshold == 65
        assert worker.auto_migrate is True

    @pytest.mark.asyncio
    async def test_init_custom_params(self):
        """커스텀 파라미터로 워커 초기화"""
        supabase = MagicMock()
        ai = MagicMock()

        worker = BatchEvaluationWorker(
            supabase_service=supabase,
            ai_service=ai,
            batch_size=15,
            min_score_threshold=70,
            auto_migrate=False
        )

        assert worker.batch_size == 15
        assert worker.min_score_threshold == 70
        assert worker.auto_migrate is False

    @pytest.mark.asyncio
    async def test_run_batch_success(self):
        """배치 평가 성공: 10개 평가 → 3개 이동 (mock)"""
        supabase = MagicMock()
        ai = MagicMock()

        # Mock get_pending_candidates (10개 반환)
        supabase.get_pending_candidates = AsyncMock(return_value=[
            {
                'id': i,
                'thought_a_id': i * 2,
                'thought_b_id': i * 2 + 1,
                'thought_a_claim': f'Claim A {i}',
                'thought_b_claim': f'Claim B {i}',
                'similarity': 0.2
            }
            for i in range(1, 11)
        ])

        # Mock score_pairs (10개 평가, 3개 고득점)
        ai.score_pairs = AsyncMock(return_value=PairScoringResult(
            pair_scores=[
                PairScore(
                    thought_a_id=i * 2,
                    thought_b_id=i * 2 + 1,
                    logical_expansion_score=70 if i <= 3 else 50,  # 3개만 65 이상
                    connection_reason=f'Connection {i}'
                )
                for i in range(1, 11)
            ]
        ))

        # Mock update_candidate_score
        supabase.update_candidate_score = AsyncMock()

        # Mock move_to_thought_pairs (3개 이동)
        supabase.move_to_thought_pairs = AsyncMock(return_value=3)

        worker = BatchEvaluationWorker(
            supabase_service=supabase,
            ai_service=ai,
            batch_size=10,
            min_score_threshold=65,
            auto_migrate=True
        )

        result = await worker.run_batch(max_candidates=10)

        assert result['evaluated'] == 10
        assert result['migrated'] == 3
        assert result['failed'] == 0

    @pytest.mark.asyncio
    async def test_run_batch_no_pending(self):
        """pending 없음: evaluated=0, 에러 없음"""
        supabase = MagicMock()
        ai = MagicMock()

        # Mock get_pending_candidates (빈 리스트)
        supabase.get_pending_candidates = AsyncMock(return_value=[])

        worker = BatchEvaluationWorker(
            supabase_service=supabase,
            ai_service=ai
        )

        result = await worker.run_batch(max_candidates=100)

        assert result['evaluated'] == 0
        assert result['migrated'] == 0
        assert result['failed'] == 0

    @pytest.mark.asyncio
    async def test_run_batch_partial_failure(self):
        """부분 실패: 10개 중 2개 실패 → 8개는 성공"""
        supabase = MagicMock()
        ai = MagicMock()

        # Mock get_pending_candidates
        supabase.get_pending_candidates = AsyncMock(return_value=[
            {
                'id': i,
                'thought_a_id': i * 2,
                'thought_b_id': i * 2 + 1,
                'thought_a_claim': f'Claim A {i}',
                'thought_b_claim': f'Claim B {i}',
                'similarity': 0.2
            }
            for i in range(1, 11)
        ])

        # Mock score_pairs (정상)
        ai.score_pairs = AsyncMock(return_value=PairScoringResult(
            pair_scores=[
                PairScore(
                    thought_a_id=i * 2,
                    thought_b_id=i * 2 + 1,
                    logical_expansion_score=50,
                    connection_reason=f'Connection {i}'
                )
                for i in range(1, 11)
            ]
        ))

        # Mock update_candidate_score (2개 실패)
        call_count = 0

        async def mock_update_with_failure(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count in [3, 7]:  # 3번째, 7번째 호출 실패
                raise Exception("DB update failed")

        supabase.update_candidate_score = AsyncMock(side_effect=mock_update_with_failure)
        supabase.move_to_thought_pairs = AsyncMock(return_value=0)

        worker = BatchEvaluationWorker(
            supabase_service=supabase,
            ai_service=ai,
            auto_migrate=True
        )

        result = await worker.run_batch(max_candidates=10)

        assert result['evaluated'] == 8  # 성공한 개수
        assert result['failed'] == 2

    @pytest.mark.asyncio
    async def test_run_batch_rate_limiting(self):
        """Rate limiting: 배치 간 0.5초 대기 확인"""
        supabase = MagicMock()
        ai = MagicMock()

        # Mock get_pending_candidates (25개 - 3개 배치)
        supabase.get_pending_candidates = AsyncMock(return_value=[
            {
                'id': i,
                'thought_a_id': i * 2,
                'thought_b_id': i * 2 + 1,
                'thought_a_claim': f'Claim A {i}',
                'thought_b_claim': f'Claim B {i}',
                'similarity': 0.2
            }
            for i in range(1, 26)
        ])

        # Mock score_pairs
        ai.score_pairs = AsyncMock(return_value=PairScoringResult(
            pair_scores=[
                PairScore(
                    thought_a_id=i * 2,
                    thought_b_id=i * 2 + 1,
                    logical_expansion_score=50,
                    connection_reason=f'Connection {i}'
                )
                for i in range(1, 11)
            ]
        ))

        supabase.update_candidate_score = AsyncMock()
        supabase.move_to_thought_pairs = AsyncMock(return_value=0)

        worker = BatchEvaluationWorker(
            supabase_service=supabase,
            ai_service=ai,
            batch_size=10
        )

        import time
        start_time = time.time()
        await worker.run_batch(max_candidates=25)
        elapsed = time.time() - start_time

        # 3개 배치 → 2번의 대기 (0.5초 × 2 = 1초)
        # 실제 처리 시간 포함하여 최소 1초 이상
        assert elapsed >= 1.0

    @pytest.mark.asyncio
    async def test_run_batch_auto_migrate_disabled(self):
        """auto_migrate=False: 고득점 후보도 이동하지 않음"""
        supabase = MagicMock()
        ai = MagicMock()

        # Mock get_pending_candidates
        supabase.get_pending_candidates = AsyncMock(return_value=[
            {
                'id': 1,
                'thought_a_id': 10,
                'thought_b_id': 20,
                'thought_a_claim': 'Claim A',
                'thought_b_claim': 'Claim B',
                'similarity': 0.2
            }
        ])

        # Mock score_pairs (고득점)
        ai.score_pairs = AsyncMock(return_value=PairScoringResult(
            pair_scores=[
                PairScore(
                    thought_a_id=10,
                    thought_b_id=20,
                    logical_expansion_score=90,  # 고득점
                    connection_reason='Strong connection'
                )
            ]
        ))

        supabase.update_candidate_score = AsyncMock()
        supabase.move_to_thought_pairs = AsyncMock(return_value=1)

        worker = BatchEvaluationWorker(
            supabase_service=supabase,
            ai_service=ai,
            auto_migrate=False  # 자동 이동 비활성화
        )

        result = await worker.run_batch(max_candidates=10)

        assert result['evaluated'] == 1
        assert result['migrated'] == 0  # 이동되지 않음
        supabase.move_to_thought_pairs.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_batch_scoring_api_failure(self):
        """Claude API 전체 실패: 배치 전체 failed"""
        supabase = MagicMock()
        ai = MagicMock()

        # Mock get_pending_candidates
        supabase.get_pending_candidates = AsyncMock(return_value=[
            {
                'id': i,
                'thought_a_id': i * 2,
                'thought_b_id': i * 2 + 1,
                'thought_a_claim': f'Claim A {i}',
                'thought_b_claim': f'Claim B {i}',
                'similarity': 0.2
            }
            for i in range(1, 11)
        ])

        # Mock score_pairs (API 실패)
        ai.score_pairs = AsyncMock(side_effect=Exception("Claude API Error"))

        worker = BatchEvaluationWorker(
            supabase_service=supabase,
            ai_service=ai
        )

        result = await worker.run_batch(max_candidates=10)

        assert result['evaluated'] == 0
        assert result['failed'] == 10

    @pytest.mark.asyncio
    async def test_run_batch_high_score_collection(self):
        """고득점 후보만 정확히 수집"""
        supabase = MagicMock()
        ai = MagicMock()

        # Mock get_pending_candidates
        supabase.get_pending_candidates = AsyncMock(return_value=[
            {
                'id': i,
                'thought_a_id': i * 2,
                'thought_b_id': i * 2 + 1,
                'thought_a_claim': f'Claim A {i}',
                'thought_b_claim': f'Claim B {i}',
                'similarity': 0.2
            }
            for i in range(1, 6)
        ])

        # Mock score_pairs (점수: 90, 70, 50, 85, 60)
        ai.score_pairs = AsyncMock(return_value=PairScoringResult(
            pair_scores=[
                PairScore(thought_a_id=2, thought_b_id=3, logical_expansion_score=90, connection_reason='Excellent connection'),
                PairScore(thought_a_id=4, thought_b_id=5, logical_expansion_score=70, connection_reason='Good connection'),
                PairScore(thought_a_id=6, thought_b_id=7, logical_expansion_score=50, connection_reason='Weak connection'),
                PairScore(thought_a_id=8, thought_b_id=9, logical_expansion_score=85, connection_reason='Strong connection'),
                PairScore(thought_a_id=10, thought_b_id=11, logical_expansion_score=60, connection_reason='Fair connection'),
            ]
        ))

        supabase.update_candidate_score = AsyncMock()

        migrated_ids = []

        async def mock_move(candidate_ids, min_score):
            migrated_ids.extend(candidate_ids)
            return len(candidate_ids)

        supabase.move_to_thought_pairs = AsyncMock(side_effect=mock_move)

        worker = BatchEvaluationWorker(
            supabase_service=supabase,
            ai_service=ai,
            min_score_threshold=65,
            auto_migrate=True
        )

        result = await worker.run_batch(max_candidates=5)

        # 65점 이상: ID 1(90점), 2(70점), 4(85점)
        assert result['migrated'] == 3
        assert set(migrated_ids) == {1, 2, 4}

    @pytest.mark.asyncio
    async def test_run_batch_empty_score_result(self):
        """Claude가 빈 결과 반환: API 에러로 처리"""
        supabase = MagicMock()
        ai = MagicMock()

        # Mock get_pending_candidates
        supabase.get_pending_candidates = AsyncMock(return_value=[
            {
                'id': 1,
                'thought_a_id': 10,
                'thought_b_id': 20,
                'thought_a_claim': 'Claim A',
                'thought_b_claim': 'Claim B',
                'similarity': 0.2
            }
        ])

        # Mock score_pairs (빈 결과 대신 API 에러)
        ai.score_pairs = AsyncMock(side_effect=Exception("Empty response from Claude"))

        worker = BatchEvaluationWorker(
            supabase_service=supabase,
            ai_service=ai
        )

        result = await worker.run_batch(max_candidates=10)

        # API 에러로 배치 전체 실패
        assert result['evaluated'] == 0
        assert result['failed'] == 1
