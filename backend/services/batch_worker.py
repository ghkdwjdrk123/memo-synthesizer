"""
배치 워커: pair_candidates를 Claude로 평가하여 고득점 항목을 thought_pairs로 이동.

주요 역할:
- pair_candidates 테이블에서 미평가 후보 조회
- Claude API를 통한 배치 평가 (10개씩)
- 평가 결과를 pair_candidates에 업데이트
- 고득점 후보를 thought_pairs로 자동 이동

사용 예시:
    worker = BatchEvaluationWorker(
        supabase_service=supabase,
        ai_service=ai,
        batch_size=10,
        min_score_threshold=65,
        auto_migrate=True
    )
    result = await worker.run_batch(max_candidates=100)
    print(f"평가: {result['evaluated']}, 이동: {result['migrated']}")
"""

import asyncio
import logging
from typing import Dict

from services.supabase_service import SupabaseService
from services.ai_service import AIService
from schemas.zk import ThoughtPairCandidate

logger = logging.getLogger(__name__)


class BatchEvaluationWorker:
    """
    배치 평가 워커.

    pair_candidates 테이블에서 미평가 후보를 가져와 Claude로 평가하고,
    고득점 항목을 thought_pairs 테이블로 이동시킵니다.

    Attributes:
        supabase: Supabase 서비스 인스턴스
        ai: AI 서비스 인스턴스
        batch_size: Claude 평가 배치 크기 (기본 10개)
        min_score_threshold: thought_pairs 이동 기준 점수 (기본 65)
        auto_migrate: 고득점 자동 이동 여부 (기본 True)
    """

    def __init__(
        self,
        supabase_service: SupabaseService,
        ai_service: AIService,
        batch_size: int = 10,
        min_score_threshold: int = 65,
        auto_migrate: bool = True
    ):
        """
        배치 워커 초기화.

        Args:
            supabase_service: Supabase 서비스 인스턴스
            ai_service: AI 서비스 인스턴스
            batch_size: Claude 평가 배치 크기 (기본 10개, 권장 5-15)
            min_score_threshold: thought_pairs 이동 기준 점수 (기본 65, standard tier)
            auto_migrate: 고득점 자동 이동 여부 (기본 True)
        """
        self.supabase = supabase_service
        self.ai = ai_service
        self.batch_size = batch_size
        self.min_score_threshold = min_score_threshold
        self.auto_migrate = auto_migrate

        logger.info(
            f"BatchEvaluationWorker initialized: "
            f"batch_size={batch_size}, "
            f"min_score_threshold={min_score_threshold}, "
            f"auto_migrate={auto_migrate}"
        )

    async def run_batch(self, max_candidates: int = 100) -> Dict[str, int]:
        """
        배치 평가 실행.

        워크플로우:
        1. pair_candidates에서 미평가 후보 조회 (llm_status='pending')
        2. batch_size씩 분할
        3. 각 배치에 대해:
           - ThoughtPairCandidate 객체 생성
           - ai_service.score_pairs() 호출
           - 결과를 update_candidate_score()로 저장
           - 고득점(>= min_score_threshold) candidate_ids 수집
        4. auto_migrate=True면 move_to_thought_pairs() 호출
        5. Rate limiting: 배치 간 0.5초 대기

        Args:
            max_candidates: 최대 처리 개수 (기본 100)

        Returns:
            Dict[str, int]: {
                "evaluated": N,  # 성공적으로 평가된 후보 수
                "migrated": M,   # thought_pairs로 이동된 후보 수
                "failed": F      # 실패한 후보 수
            }

        Example:
            >>> worker = BatchEvaluationWorker(supabase, ai)
            >>> result = await worker.run_batch(max_candidates=50)
            >>> print(f"평가 완료: {result['evaluated']}개")
            >>> print(f"이동 완료: {result['migrated']}개")
        """
        result = {"evaluated": 0, "migrated": 0, "failed": 0}

        # Step 1: 미평가 후보 조회
        logger.info(f"Fetching up to {max_candidates} pending candidates...")

        try:
            pending = await self.supabase.get_pending_candidates(limit=max_candidates)
        except Exception as e:
            logger.error(f"Failed to fetch pending candidates: {e}")
            return result

        if not pending:
            logger.info("No pending candidates found")
            return result

        logger.info(
            f"Found {len(pending)} pending candidates "
            f"(will process in batches of {self.batch_size})"
        )

        # Step 2: 배치 단위로 처리
        total_batches = (len(pending) + self.batch_size - 1) // self.batch_size

        for i in range(0, len(pending), self.batch_size):
            batch = pending[i:i+self.batch_size]
            batch_num = (i // self.batch_size) + 1

            logger.info(
                f"Processing batch {batch_num}/{total_batches} "
                f"({len(batch)} candidates)..."
            )

            try:
                # Step 3: ThoughtPairCandidate 변환
                candidates = [
                    ThoughtPairCandidate(
                        thought_a_id=c["thought_a_id"],
                        thought_b_id=c["thought_b_id"],
                        thought_a_claim=c["thought_a_claim"],
                        thought_b_claim=c["thought_b_claim"],
                        similarity_score=c["similarity"]
                    )
                    for c in batch
                ]

                # Step 4: Claude 평가
                logger.info(f"Calling Claude API to score {len(candidates)} pairs...")
                scoring_result = await self.ai.score_pairs(candidates)

                # Step 5: 결과 저장
                high_score_ids = []

                for score in scoring_result.pair_scores:
                    # 원본 candidate 찾기
                    candidate = next(
                        (c for c in batch
                         if c["thought_a_id"] == score.thought_a_id
                         and c["thought_b_id"] == score.thought_b_id),
                        None
                    )

                    if not candidate:
                        logger.warning(
                            f"Scored pair not found in batch: "
                            f"thought_a={score.thought_a_id}, "
                            f"thought_b={score.thought_b_id}"
                        )
                        result["failed"] += 1
                        continue

                    try:
                        # DB 업데이트
                        await self.supabase.update_candidate_score(
                            candidate_id=candidate["id"],
                            llm_score=score.logical_expansion_score,
                            connection_reason=score.connection_reason
                        )
                        result["evaluated"] += 1

                        # 고득점 후보 수집
                        if score.logical_expansion_score >= self.min_score_threshold:
                            high_score_ids.append(candidate["id"])
                            logger.info(
                                f"High score candidate: ID={candidate['id']}, "
                                f"score={score.logical_expansion_score}"
                            )

                    except Exception as e:
                        logger.error(
                            f"Failed to update candidate {candidate['id']}: {e}"
                        )
                        result["failed"] += 1

                # Step 6: 고득점 후보 이동 (배치별)
                if self.auto_migrate and high_score_ids:
                    try:
                        migrated = await self.supabase.move_to_thought_pairs(
                            candidate_ids=high_score_ids,
                            min_score=self.min_score_threshold
                        )
                        result["migrated"] += migrated
                        logger.info(
                            f"Migrated {migrated} high-score pairs to thought_pairs "
                            f"(batch {batch_num}/{total_batches})"
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to migrate high-score pairs: {e}"
                        )

            except Exception as e:
                # 배치 전체 실패
                logger.error(
                    f"Batch {batch_num}/{total_batches} failed: {e}"
                )
                result["failed"] += len(batch)

            # Step 7: Rate limiting (배치 간 대기)
            if i + self.batch_size < len(pending):
                logger.debug("Waiting 0.5s before next batch (rate limiting)...")
                await asyncio.sleep(0.5)

        # 최종 결과 로깅
        logger.info(
            f"Batch evaluation completed: "
            f"evaluated={result['evaluated']}, "
            f"migrated={result['migrated']}, "
            f"failed={result['failed']} "
            f"(total_candidates={len(pending)})"
        )

        return result
