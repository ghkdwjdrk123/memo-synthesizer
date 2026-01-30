"""
Sampling strategy for thought pair candidates (상대적 백분위수 기반)
"""
import logging
from typing import Any, Dict, List, Tuple, Optional
from collections import defaultdict
import random

logger = logging.getLogger(__name__)


class SamplingStrategy:
    """
    사고 단위 페어 후보 샘플링 전략 (백분위수 기반)

    유사도 구간을 절대값이 아닌 백분위수로 동적 분할
    raw_note 다양성을 고려한 샘플링

    Usage:
        from services.distribution_service import DistributionService
        dist_service = DistributionService(supabase_service)
        strategy = SamplingStrategy(distribution_service=dist_service)
        sampled = await strategy.sample_initial(candidates, target_count=100, strategy="p10_p40")
    """

    def __init__(
        self,
        distribution_service,
        low_ratio: float = 0.4,
        mid_ratio: float = 0.35,
        high_ratio: float = 0.25
    ):
        """
        Args:
            distribution_service: DistributionService 인스턴스
            low_ratio: 낮은 구간 샘플링 비율 (40%)
            mid_ratio: 중간 구간 샘플링 비율 (35%)
            high_ratio: 높은 구간 샘플링 비율 (25%)
        """
        self.dist_service = distribution_service
        self.low_ratio = low_ratio
        self.mid_ratio = mid_ratio
        self.high_ratio = high_ratio

        # 비율 합계 검증
        total_ratio = low_ratio + mid_ratio + high_ratio
        if abs(total_ratio - 1.0) > 0.001:
            raise ValueError(f"비율 합계가 1.0이 아닙니다: {total_ratio}")

    async def sample_initial(
        self,
        candidates: List[Dict[str, Any]],
        target_count: int = 100,
        strategy: str = "p10_p40",
        custom_range: Optional[Tuple[int, int]] = None
    ) -> List[Dict[str, Any]]:
        """
        초기 샘플링 수행 (백분위수 기반)

        Args:
            candidates: pair_candidates 레코드 목록
                - id: int
                - thought_a_id: int
                - thought_b_id: int
                - similarity: float
                - raw_note_id_a: str (UUID)
                - raw_note_id_b: str (UUID)
            target_count: 목표 샘플 개수
            strategy: 백분위수 전략
                - "p10_p40": 하위 10-40% (기본, 창의적 조합)
                - "p30_p60": 하위 30-60% (안전한 조합)
                - "p0_p30": 최하위 30%
                - "custom": custom_range 사용
            custom_range: 커스텀 백분위수 범위 (예: (20, 50))

        Returns:
            샘플링된 후보 목록 (원본 레코드 유지)
        """
        # 엣지 케이스: candidates가 target보다 적으면 전체 반환
        if len(candidates) <= target_count:
            logger.info(
                f"후보 개수({len(candidates)})가 목표({target_count})보다 적어 전체 반환"
            )
            return candidates

        # 1. 전체 분포 조회
        dist = await self.dist_service.get_distribution()
        percentiles = dist["percentiles"]

        # 2. 3-tier 구간 백분위수 직접 사용 (strategy에 따라 동적 계산)
        if strategy == "p10_p40":
            low_range = (percentiles["p10"], percentiles["p20"])
            mid_range = (percentiles["p20"], percentiles["p30"])
            high_range = (percentiles["p30"], percentiles["p40"])
        elif strategy == "p30_p60":
            low_range = (percentiles["p30"], percentiles["p40"])
            mid_range = (percentiles["p40"], percentiles["p50"])
            high_range = (percentiles["p50"], percentiles["p60"])
        elif strategy == "p0_p30":
            low_range = (percentiles["p0"], percentiles["p10"])
            mid_range = (percentiles["p10"], percentiles["p20"])
            high_range = (percentiles["p20"], percentiles["p30"])
        elif strategy == "custom":
            if not custom_range:
                raise ValueError("custom_range required for custom strategy")
            min_pct, max_pct = custom_range
            # Custom은 3등분 방식 사용
            min_sim = percentiles[f"p{min_pct}"]
            max_sim = percentiles[f"p{max_pct}"]
            range_width = (max_sim - min_sim) / 3.0
            low_range = (min_sim, min_sim + range_width)
            mid_range = (min_sim + range_width, min_sim + 2 * range_width)
            high_range = (min_sim + 2 * range_width, max_sim)
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

        logger.info(
            f"Dynamic tier ranges ({strategy}): "
            f"Low={low_range}, Mid={mid_range}, High={high_range}"
        )

        # 3. 유사도 구간별 분할
        low_group = self._filter_by_similarity(candidates, *low_range)
        mid_group = self._filter_by_similarity(candidates, *mid_range)
        high_group = self._filter_by_similarity(candidates, *high_range)

        logger.info(
            f"유사도 구간별 후보 수 - Low: {len(low_group)}, "
            f"Mid: {len(mid_group)}, High: {len(high_group)}"
        )

        # 4. 각 구간별 목표 개수 계산
        low_target = int(target_count * self.low_ratio)
        mid_target = int(target_count * self.mid_ratio)
        high_target = int(target_count * self.high_ratio)

        # 반올림 오차 보정 (남은 개수를 Low에 추가)
        remaining = target_count - (low_target + mid_target + high_target)
        low_target += remaining

        # 5. 각 구간에서 다양성 샘플링
        sampled_low = self._diverse_sample(low_group, low_target)
        sampled_mid = self._diverse_sample(mid_group, mid_target)
        sampled_high = self._diverse_sample(high_group, high_target)

        # 6. 결과 합치기
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
