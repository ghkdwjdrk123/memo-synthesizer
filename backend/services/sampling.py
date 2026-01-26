"""
Sampling strategy for thought pair candidates
"""
import logging
from typing import Any, Dict, List, Tuple
from collections import defaultdict
import random

logger = logging.getLogger(__name__)


class SamplingStrategy:
    """
    사고 단위 페어 후보 샘플링 전략

    유사도 구간별 분할 및 raw_note 다양성을 고려한 샘플링

    Usage:
        strategy = SamplingStrategy()
        sampled = strategy.sample_initial(candidates, target_count=100)
    """

    def __init__(
        self,
        low_range: Tuple[float, float] = (0.05, 0.15),
        mid_range: Tuple[float, float] = (0.15, 0.25),
        high_range: Tuple[float, float] = (0.25, 0.35),
        low_ratio: float = 0.4,
        mid_ratio: float = 0.35,
        high_ratio: float = 0.25
    ):
        """
        Args:
            low_range: 낮은 유사도 구간 (창의적 조합)
            mid_range: 중간 유사도 구간
            high_range: 높은 유사도 구간
            low_ratio: 낮은 구간 샘플링 비율 (40%)
            mid_ratio: 중간 구간 샘플링 비율 (35%)
            high_ratio: 높은 구간 샘플링 비율 (25%)
        """
        self.low_range = low_range
        self.mid_range = mid_range
        self.high_range = high_range
        self.low_ratio = low_ratio
        self.mid_ratio = mid_ratio
        self.high_ratio = high_ratio

        # 비율 합계 검증
        total_ratio = low_ratio + mid_ratio + high_ratio
        if abs(total_ratio - 1.0) > 0.001:
            raise ValueError(f"비율 합계가 1.0이 아닙니다: {total_ratio}")

    def sample_initial(
        self,
        candidates: List[Dict[str, Any]],
        target_count: int = 100
    ) -> List[Dict[str, Any]]:
        """
        초기 샘플링 수행

        Args:
            candidates: pair_candidates 레코드 목록
                - id: int
                - thought_a_id: int
                - thought_b_id: int
                - similarity: float
                - raw_note_id_a: str (UUID)
                - raw_note_id_b: str (UUID)
            target_count: 목표 샘플 개수

        Returns:
            샘플링된 후보 목록 (원본 레코드 유지)
        """
        # 엣지 케이스: candidates가 target보다 적으면 전체 반환
        if len(candidates) <= target_count:
            logger.info(
                f"후보 개수({len(candidates)})가 목표({target_count})보다 적어 전체 반환"
            )
            return candidates

        # 1. 유사도 구간별 분할
        low_group = self._filter_by_similarity(candidates, *self.low_range)
        mid_group = self._filter_by_similarity(candidates, *self.mid_range)
        high_group = self._filter_by_similarity(candidates, *self.high_range)

        logger.info(
            f"유사도 구간별 후보 수 - Low: {len(low_group)}, "
            f"Mid: {len(mid_group)}, High: {len(high_group)}"
        )

        # 2. 각 구간별 목표 개수 계산
        low_target = int(target_count * self.low_ratio)
        mid_target = int(target_count * self.mid_ratio)
        high_target = int(target_count * self.high_ratio)

        # 반올림 오차 보정 (남은 개수를 Low에 추가)
        remaining = target_count - (low_target + mid_target + high_target)
        low_target += remaining

        # 3. 각 구간에서 다양성 샘플링
        sampled_low = self._diverse_sample(low_group, low_target)
        sampled_mid = self._diverse_sample(mid_group, mid_target)
        sampled_high = self._diverse_sample(high_group, high_target)

        # 4. 결과 합치기
        result = sampled_low + sampled_mid + sampled_high

        logger.info(
            f"샘플링 완료 - Low: {len(sampled_low)}/{low_target}, "
            f"Mid: {len(sampled_mid)}/{mid_target}, "
            f"High: {len(sampled_high)}/{high_target}, "
            f"Total: {len(result)}/{target_count}"
        )

        return result

    def _filter_by_similarity(
        self,
        candidates: List[Dict[str, Any]],
        min_sim: float,
        max_sim: float
    ) -> List[Dict[str, Any]]:
        """
        유사도 범위로 후보 필터링

        Args:
            candidates: 후보 목록
            min_sim: 최소 유사도 (포함)
            max_sim: 최대 유사도 (미포함)

        Returns:
            필터링된 후보 목록
        """
        return [
            c for c in candidates
            if min_sim <= c['similarity'] < max_sim
        ]

    def _diverse_sample(
        self,
        candidates: List[Dict[str, Any]],
        target: int
    ) -> List[Dict[str, Any]]:
        """
        raw_note 다양성을 고려한 샘플링

        동일 raw_note 조합을 최소화하기 위해 Round-robin 방식 사용

        Args:
            candidates: 후보 목록
            target: 목표 샘플 개수

        Returns:
            샘플링된 후보 목록
        """
        # 엣지 케이스: candidates가 target보다 적으면 전체 반환
        if len(candidates) <= target:
            return candidates

        # 1. raw_note 조합별로 그룹화
        # key: (raw_note_id_a, raw_note_id_b) 튜플
        groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)

        for candidate in candidates:
            key = (candidate['raw_note_id_a'], candidate['raw_note_id_b'])
            groups[key].append(candidate)

        # 2. 각 그룹 내에서 셔플 (랜덤성 확보)
        for group_candidates in groups.values():
            random.shuffle(group_candidates)

        # 3. Round-robin 샘플링
        result: List[Dict[str, Any]] = []
        group_keys = list(groups.keys())
        indices = {key: 0 for key in group_keys}  # 각 그룹의 현재 인덱스

        # 모든 그룹을 순회하며 하나씩 추출
        round_num = 0
        while len(result) < target:
            added_in_round = 0

            for key in group_keys:
                # 목표 개수 도달 시 종료
                if len(result) >= target:
                    break

                # 현재 그룹에서 샘플 추출
                group = groups[key]
                idx = indices[key]

                if idx < len(group):
                    result.append(group[idx])
                    indices[key] += 1
                    added_in_round += 1

            # 더 이상 추출할 샘플이 없으면 종료
            if added_in_round == 0:
                logger.warning(
                    f"목표 개수({target})에 도달하지 못했습니다. "
                    f"현재 샘플 수: {len(result)}"
                )
                break

            round_num += 1

        return result
