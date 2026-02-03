"""
샘플링 기반 후보 마이닝 서비스

기존 Distance Table 방식(전쌍 계산)을 대체하는 새로운 접근법:
- src당 10-20개 후보 생성 (O(N×k) vs O(N²))
- 키셋 페이징으로 재개 가능
- rand_key 기반 결정론적 샘플링
- 배치 내 분위수 계산으로 밴드 필터링
"""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class CandidateMiningService:
    """샘플링 기반 후보 마이닝 서비스"""

    # 기본 파라미터
    DEFAULT_SRC_BATCH = 30
    DEFAULT_DST_SAMPLE = 1200
    DEFAULT_K_PER_SRC = 15
    DEFAULT_P_LO = 0.10
    DEFAULT_P_HI = 0.35
    DEFAULT_SEED = 42
    DEFAULT_MAX_ROUNDS = 3

    def __init__(self, supabase_service):
        self.supabase = supabase_service

    async def mine_batch(
        self,
        last_src_id: int = 0,
        src_batch: int = None,
        dst_sample: int = None,
        k: int = None,
        p_lo: float = None,
        p_hi: float = None,
        seed: int = None,
        max_rounds: int = None
    ) -> Dict[str, Any]:
        """
        단일 배치 마이닝 실행

        Args:
            last_src_id: 마지막 처리한 src ID (키셋 페이징)
            src_batch: 배치당 src 수 (기본 30)
            dst_sample: dst 샘플 크기 (기본 1200)
            k: src당 후보 수 (기본 15)
            p_lo: 하위 분위수 (기본 0.10)
            p_hi: 상위 분위수 (기본 0.35)
            seed: 결정론적 샘플링용 시드 (기본 42)
            max_rounds: 최대 재시도 횟수 (기본 3)

        Returns:
            {
                "success": bool,
                "new_last_src_id": int,
                "inserted_count": int,
                "src_processed_count": int,
                "rounds_used": int,
                "band_lo": float,
                "band_hi": float,
                "avg_candidates_per_src": float,
                "duration_ms": int
            }
        """
        # 기본값 적용
        params = {
            "p_last_src_id": last_src_id,
            "p_src_batch": src_batch or self.DEFAULT_SRC_BATCH,
            "p_dst_sample": dst_sample or self.DEFAULT_DST_SAMPLE,
            "p_k": k or self.DEFAULT_K_PER_SRC,
            "p_lo": p_lo or self.DEFAULT_P_LO,
            "p_hi": p_hi or self.DEFAULT_P_HI,
            "p_seed": seed or self.DEFAULT_SEED,
            "p_max_rounds": max_rounds or self.DEFAULT_MAX_ROUNDS
        }

        logger.info(f"Starting mine_batch: last_src_id={last_src_id}, params={params}")

        try:
            result = await self.supabase.mine_candidate_pairs(**params)

            if result.get("success"):
                logger.info(
                    f"Mine batch completed: "
                    f"{result.get('inserted_count')} candidates, "
                    f"{result.get('src_processed_count')} sources, "
                    f"{result.get('duration_ms')}ms"
                )
            else:
                logger.error(f"Mine batch failed: {result.get('error')}")

            return result

        except Exception as e:
            logger.error(f"Mine batch exception: {e}")
            return {
                "success": False,
                "error": str(e),
                "new_last_src_id": last_src_id
            }

    async def mine_full(
        self,
        src_batch: int = None,
        dst_sample: int = None,
        k: int = None,
        p_lo: float = None,
        p_hi: float = None,
        seed: int = None,
        max_rounds: int = None,
        progress_callback: callable = None
    ) -> Dict[str, Any]:
        """
        전체 마이닝 실행 (모든 thought 처리)

        Args:
            ...: mine_batch와 동일
            progress_callback: 진행 상황 콜백 (선택)
                async def callback(progress: dict) -> None

        Returns:
            {
                "success": bool,
                "total_src_processed": int,
                "total_pairs_inserted": int,
                "total_batches": int,
                "total_duration_ms": int,
                "avg_candidates_per_src": float
            }
        """
        import time

        start_time = time.time()

        # 파라미터 설정
        _src_batch = src_batch or self.DEFAULT_SRC_BATCH
        _dst_sample = dst_sample or self.DEFAULT_DST_SAMPLE
        _k = k or self.DEFAULT_K_PER_SRC
        _p_lo = p_lo or self.DEFAULT_P_LO
        _p_hi = p_hi or self.DEFAULT_P_HI
        _seed = seed or self.DEFAULT_SEED
        _max_rounds = max_rounds or self.DEFAULT_MAX_ROUNDS

        # 진행 상태 초기화
        progress_id = await self._create_progress_record(
            src_batch=_src_batch,
            dst_sample=_dst_sample,
            k_per_src=_k,
            p_lo=_p_lo,
            p_hi=_p_hi,
            max_rounds=_max_rounds,
            seed=_seed
        )

        last_src_id = 0
        total_src_processed = 0
        total_pairs_inserted = 0
        batch_count = 0

        logger.info(f"Starting full mining: progress_id={progress_id}")

        try:
            while True:
                batch_count += 1

                result = await self.mine_batch(
                    last_src_id=last_src_id,
                    src_batch=_src_batch,
                    dst_sample=_dst_sample,
                    k=_k,
                    p_lo=_p_lo,
                    p_hi=_p_hi,
                    seed=_seed,
                    max_rounds=_max_rounds
                )

                if not result.get("success"):
                    # 배치 실패 시 중단
                    await self._update_progress_record(
                        progress_id,
                        status="failed",
                        last_src_id=last_src_id,
                        total_src_processed=total_src_processed,
                        total_pairs_inserted=total_pairs_inserted,
                        error_message=result.get("error")
                    )
                    return {
                        "success": False,
                        "error": result.get("error"),
                        "total_src_processed": total_src_processed,
                        "total_pairs_inserted": total_pairs_inserted,
                        "total_batches": batch_count - 1
                    }

                # 집계
                src_processed = result.get("src_processed_count", 0)
                pairs_inserted = result.get("inserted_count", 0)
                new_last_src_id = result.get("new_last_src_id", last_src_id)

                total_src_processed += src_processed
                total_pairs_inserted += pairs_inserted

                # 진행 상황 업데이트
                await self._update_progress_record(
                    progress_id,
                    status="in_progress",
                    last_src_id=new_last_src_id,
                    total_src_processed=total_src_processed,
                    total_pairs_inserted=total_pairs_inserted,
                    avg_candidates_per_src=(
                        total_pairs_inserted / total_src_processed
                        if total_src_processed > 0 else 0
                    )
                )

                # 콜백 호출
                if progress_callback:
                    try:
                        await progress_callback({
                            "batch": batch_count,
                            "last_src_id": new_last_src_id,
                            "total_src_processed": total_src_processed,
                            "total_pairs_inserted": total_pairs_inserted
                        })
                    except Exception as e:
                        logger.warning(f"Progress callback error: {e}")

                # 종료 조건: 더 이상 처리할 src 없음
                if src_processed == 0 or new_last_src_id == last_src_id:
                    logger.info(
                        f"Mining completed: no more sources after id={new_last_src_id}"
                    )
                    break

                last_src_id = new_last_src_id

                # 배치 간 로깅
                if batch_count % 10 == 0:
                    elapsed = time.time() - start_time
                    logger.info(
                        f"Progress: batch {batch_count}, "
                        f"src={total_src_processed}, pairs={total_pairs_inserted}, "
                        f"elapsed={elapsed:.1f}s"
                    )

            # 완료 처리
            total_duration_ms = int((time.time() - start_time) * 1000)

            await self._update_progress_record(
                progress_id,
                status="completed",
                last_src_id=last_src_id,
                total_src_processed=total_src_processed,
                total_pairs_inserted=total_pairs_inserted,
                avg_candidates_per_src=(
                    total_pairs_inserted / total_src_processed
                    if total_src_processed > 0 else 0
                )
            )

            result = {
                "success": True,
                "progress_id": progress_id,
                "total_src_processed": total_src_processed,
                "total_pairs_inserted": total_pairs_inserted,
                "total_batches": batch_count,
                "total_duration_ms": total_duration_ms,
                "avg_candidates_per_src": (
                    round(total_pairs_inserted / total_src_processed, 2)
                    if total_src_processed > 0 else 0
                )
            }

            logger.info(
                f"Full mining completed: {result}"
            )

            return result

        except Exception as e:
            logger.error(f"Full mining exception: {e}")
            await self._update_progress_record(
                progress_id,
                status="failed",
                error_message=str(e)
            )
            return {
                "success": False,
                "error": str(e),
                "total_src_processed": total_src_processed,
                "total_pairs_inserted": total_pairs_inserted,
                "total_batches": batch_count
            }

    async def get_progress(self) -> Optional[Dict[str, Any]]:
        """
        최신 마이닝 진행 상태 조회

        Returns:
            {
                "id": int,
                "run_id": str,
                "status": "pending"|"in_progress"|"completed"|"paused"|"failed",
                "last_src_id": int,
                "total_src_processed": int,
                "total_pairs_inserted": int,
                "avg_candidates_per_src": float,
                "params": {...},
                "started_at": str,
                "updated_at": str,
                "completed_at": str|None,
                "error_message": str|None
            }
        """
        try:
            result = await self.supabase.get_mining_progress()
            return result
        except Exception as e:
            logger.error(f"Failed to get mining progress: {e}")
            return None

    async def resume_mining(self) -> Dict[str, Any]:
        """
        중단된 마이닝 재개

        Returns:
            mine_full()과 동일한 반환값
        """
        progress = await self.get_progress()

        if not progress:
            return {
                "success": False,
                "error": "No mining progress to resume"
            }

        if progress.get("status") == "completed":
            return {
                "success": False,
                "error": "Mining already completed"
            }

        if progress.get("status") != "in_progress" and progress.get("status") != "paused":
            return {
                "success": False,
                "error": f"Cannot resume from status: {progress.get('status')}"
            }

        # 파라미터 복원
        last_src_id = progress.get("last_src_id", 0)

        logger.info(f"Resuming mining from last_src_id={last_src_id}")

        # mine_full 호출 (단, 전체 재시작이 아닌 중단점부터)
        # TODO: 진행 상태 ID 유지하며 재개하는 로직 추가
        return await self.mine_full(
            src_batch=progress.get("src_batch"),
            dst_sample=progress.get("dst_sample"),
            k=progress.get("k_per_src"),
            p_lo=progress.get("p_lo"),
            p_hi=progress.get("p_hi"),
            seed=progress.get("seed"),
            max_rounds=progress.get("max_rounds")
        )

    async def _create_progress_record(
        self,
        src_batch: int,
        dst_sample: int,
        k_per_src: int,
        p_lo: float,
        p_hi: float,
        max_rounds: int,
        seed: int
    ) -> int:
        """진행 상태 레코드 생성"""
        try:
            result = await self.supabase.create_mining_progress(
                src_batch=src_batch,
                dst_sample=dst_sample,
                k_per_src=k_per_src,
                p_lo=p_lo,
                p_hi=p_hi,
                max_rounds=max_rounds,
                seed=seed
            )
            return result.get("id")
        except Exception as e:
            logger.error(f"Failed to create progress record: {e}")
            return None

    async def _update_progress_record(
        self,
        progress_id: int,
        status: str,
        last_src_id: int = None,
        total_src_processed: int = None,
        total_pairs_inserted: int = None,
        avg_candidates_per_src: float = None,
        error_message: str = None
    ) -> None:
        """진행 상태 레코드 업데이트"""
        if not progress_id:
            return

        try:
            await self.supabase.update_mining_progress(
                progress_id=progress_id,
                status=status,
                last_src_id=last_src_id,
                total_src_processed=total_src_processed,
                total_pairs_inserted=total_pairs_inserted,
                avg_candidates_per_src=avg_candidates_per_src,
                error_message=error_message
            )
        except Exception as e:
            logger.error(f"Failed to update progress record: {e}")


# ============================================================
# Dependency Injection
# ============================================================

_candidate_mining_service: Optional["CandidateMiningService"] = None


def get_candidate_mining_service() -> CandidateMiningService:
    """
    CandidateMiningService 싱글톤 인스턴스 반환

    FastAPI Depends에서 사용
    """
    from services.supabase_service import get_supabase_service

    global _candidate_mining_service
    if _candidate_mining_service is None:
        supabase_service = get_supabase_service()
        _candidate_mining_service = CandidateMiningService(supabase_service)
    return _candidate_mining_service
