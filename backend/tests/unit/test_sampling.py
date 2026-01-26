"""
Unit tests for SamplingStrategy class.

하이브리드 C 전략 - 샘플링 전략 테스트
"""

import pytest
from services.sampling import SamplingStrategy


class TestSamplingStrategy:
    """SamplingStrategy 클래스 테스트"""

    def test_init_default_params(self):
        """기본 파라미터로 초기화 성공"""
        strategy = SamplingStrategy()

        assert strategy.low_range == (0.05, 0.15)
        assert strategy.mid_range == (0.15, 0.25)
        assert strategy.high_range == (0.25, 0.35)
        assert strategy.low_ratio == 0.4
        assert strategy.mid_ratio == 0.35
        assert strategy.high_ratio == 0.25

    def test_init_custom_params(self):
        """커스텀 파라미터로 초기화 성공"""
        strategy = SamplingStrategy(
            low_range=(0.1, 0.2),
            mid_range=(0.2, 0.3),
            high_range=(0.3, 0.4),
            low_ratio=0.5,
            mid_ratio=0.3,
            high_ratio=0.2
        )

        assert strategy.low_range == (0.1, 0.2)
        assert strategy.low_ratio == 0.5

    def test_init_invalid_ratio_sum(self):
        """비율 합계가 1.0이 아니면 ValueError 발생"""
        with pytest.raises(ValueError, match="비율 합계가 1.0이 아닙니다"):
            SamplingStrategy(
                low_ratio=0.5,
                mid_ratio=0.3,
                high_ratio=0.1  # 합계 0.9
            )

    def test_sample_initial_basic(self):
        """기본 샘플링: 100개 목표 → 정확히 100개 반환"""
        strategy = SamplingStrategy()

        # 300개 후보 (Low: 120개, Mid: 105개, High: 75개)
        candidates = []
        for i in range(120):
            candidates.append({
                'id': i,
                'similarity': 0.1,
                'raw_note_id_a': f'note-{i % 10}',
                'raw_note_id_b': f'note-{(i % 10) + 1}'
            })
        for i in range(120, 225):
            candidates.append({
                'id': i,
                'similarity': 0.2,
                'raw_note_id_a': f'note-{i % 10}',
                'raw_note_id_b': f'note-{(i % 10) + 1}'
            })
        for i in range(225, 300):
            candidates.append({
                'id': i,
                'similarity': 0.3,
                'raw_note_id_a': f'note-{i % 10}',
                'raw_note_id_b': f'note-{(i % 10) + 1}'
            })

        result = strategy.sample_initial(candidates, target_count=100)

        assert len(result) == 100

    def test_sample_initial_diversity(self):
        """다양성 샘플링: raw_note 조합 균등 분포 확인"""
        strategy = SamplingStrategy()

        # 동일 조합이 여러 개 있는 후보
        candidates = []
        # 그룹 A: 50개 (note-1, note-2)
        for i in range(50):
            candidates.append({
                'id': i,
                'similarity': 0.1,
                'raw_note_id_a': 'note-1',
                'raw_note_id_b': 'note-2'
            })
        # 그룹 B: 50개 (note-3, note-4)
        for i in range(50, 100):
            candidates.append({
                'id': i,
                'similarity': 0.1,
                'raw_note_id_a': 'note-3',
                'raw_note_id_b': 'note-4'
            })

        result = strategy.sample_initial(candidates, target_count=40)

        # 각 그룹에서 균등하게 샘플링되어야 함
        group_a_count = sum(1 for r in result if r['raw_note_id_a'] == 'note-1')
        group_b_count = sum(1 for r in result if r['raw_note_id_a'] == 'note-3')

        assert abs(group_a_count - group_b_count) <= 1  # 최대 1개 차이

    def test_sample_initial_similarity_distribution(self):
        """유사도 구간별 비율 검증: Low 40%, Mid 35%, High 25%"""
        strategy = SamplingStrategy()

        # 각 구간에 충분한 후보 생성 (각 200개)
        candidates = []
        for i in range(200):
            candidates.append({
                'id': i,
                'similarity': 0.1,
                'raw_note_id_a': f'note-{i}',
                'raw_note_id_b': f'note-{i+1}'
            })
        for i in range(200, 400):
            candidates.append({
                'id': i,
                'similarity': 0.2,
                'raw_note_id_a': f'note-{i}',
                'raw_note_id_b': f'note-{i+1}'
            })
        for i in range(400, 600):
            candidates.append({
                'id': i,
                'similarity': 0.3,
                'raw_note_id_a': f'note-{i}',
                'raw_note_id_b': f'note-{i+1}'
            })

        result = strategy.sample_initial(candidates, target_count=100)

        # 구간별 개수 확인
        low_count = sum(1 for r in result if 0.05 <= r['similarity'] < 0.15)
        mid_count = sum(1 for r in result if 0.15 <= r['similarity'] < 0.25)
        high_count = sum(1 for r in result if 0.25 <= r['similarity'] < 0.35)

        # 반올림 오차 고려 (±2)
        assert abs(low_count - 40) <= 2
        assert abs(mid_count - 35) <= 2
        assert abs(high_count - 25) <= 2

    def test_sample_initial_insufficient_candidates(self):
        """후보 < 목표: 전체 반환 (에러 없음)"""
        strategy = SamplingStrategy()

        candidates = [
            {'id': 1, 'similarity': 0.1, 'raw_note_id_a': 'note-1', 'raw_note_id_b': 'note-2'},
            {'id': 2, 'similarity': 0.2, 'raw_note_id_a': 'note-3', 'raw_note_id_b': 'note-4'},
            {'id': 3, 'similarity': 0.3, 'raw_note_id_a': 'note-5', 'raw_note_id_b': 'note-6'}
        ]

        result = strategy.sample_initial(candidates, target_count=100)

        assert len(result) == 3
        assert result == candidates

    def test_sample_initial_empty_candidates(self):
        """빈 리스트 입력: 빈 리스트 반환"""
        strategy = SamplingStrategy()

        result = strategy.sample_initial([], target_count=100)

        assert result == []

    def test_sample_initial_single_group(self):
        """단일 유사도 구간만 있는 경우 처리"""
        strategy = SamplingStrategy()

        # Low 구간만 있는 후보 200개 (다양한 raw_note 조합)
        candidates = []
        for i in range(200):
            candidates.append({
                'id': i,
                'similarity': 0.1,
                'raw_note_id_a': f'note-{i}',
                'raw_note_id_b': f'note-{i+1}'
            })

        result = strategy.sample_initial(candidates, target_count=50)

        # Low 구간에서만 샘플링되어야 함
        # Round-robin으로 인해 고유 조합 개수에 따라 결과가 달라질 수 있음
        assert len(result) <= 50  # 최대 50개
        assert all(0.05 <= r['similarity'] < 0.15 for r in result)

    def test_filter_by_similarity(self):
        """유사도 범위 필터링 정확성"""
        strategy = SamplingStrategy()

        candidates = [
            {'id': 1, 'similarity': 0.05},
            {'id': 2, 'similarity': 0.10},
            {'id': 3, 'similarity': 0.15},
            {'id': 4, 'similarity': 0.20}
        ]

        result = strategy._filter_by_similarity(candidates, 0.05, 0.15)

        # 0.05 <= similarity < 0.15 (0.15는 미포함)
        assert len(result) == 2
        assert result[0]['id'] == 1
        assert result[1]['id'] == 2

    def test_diverse_sample_round_robin(self):
        """Round-robin 샘플링 동작 확인"""
        strategy = SamplingStrategy()

        # 2개 그룹, 각 10개씩
        candidates = []
        for i in range(10):
            candidates.append({
                'id': f'group1-{i}',
                'similarity': 0.1,
                'raw_note_id_a': 'note-1',
                'raw_note_id_b': 'note-2'
            })
        for i in range(10):
            candidates.append({
                'id': f'group2-{i}',
                'similarity': 0.1,
                'raw_note_id_a': 'note-3',
                'raw_note_id_b': 'note-4'
            })

        result = strategy._diverse_sample(candidates, target=10)

        # 각 그룹에서 5개씩 샘플링되어야 함
        group1_count = sum(1 for r in result if r['raw_note_id_a'] == 'note-1')
        group2_count = sum(1 for r in result if r['raw_note_id_a'] == 'note-3')

        assert group1_count == 5
        assert group2_count == 5

    def test_diverse_sample_uneven_groups(self):
        """불균등한 그룹 크기에서도 다양성 유지"""
        strategy = SamplingStrategy()

        # 그룹 A: 20개, 그룹 B: 5개
        candidates = []
        for i in range(20):
            candidates.append({
                'id': f'group1-{i}',
                'similarity': 0.1,
                'raw_note_id_a': 'note-1',
                'raw_note_id_b': 'note-2'
            })
        for i in range(5):
            candidates.append({
                'id': f'group2-{i}',
                'similarity': 0.1,
                'raw_note_id_a': 'note-3',
                'raw_note_id_b': 'note-4'
            })

        result = strategy._diverse_sample(candidates, target=10)

        # 그룹 B는 5개 전부, 그룹 A는 5개 샘플링
        group1_count = sum(1 for r in result if r['raw_note_id_a'] == 'note-1')
        group2_count = sum(1 for r in result if r['raw_note_id_a'] == 'note-3')

        assert group1_count == 5
        assert group2_count == 5

    def test_sample_initial_target_one(self):
        """목표 개수 1개일 때 정상 동작"""
        strategy = SamplingStrategy()

        candidates = [
            {'id': 1, 'similarity': 0.1, 'raw_note_id_a': 'note-1', 'raw_note_id_b': 'note-2'},
            {'id': 2, 'similarity': 0.2, 'raw_note_id_a': 'note-3', 'raw_note_id_b': 'note-4'}
        ]

        result = strategy.sample_initial(candidates, target_count=1)

        assert len(result) == 1
        assert result[0] in candidates
