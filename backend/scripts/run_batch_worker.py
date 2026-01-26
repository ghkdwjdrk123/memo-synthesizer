"""
Standalone batch worker for candidate evaluation.

하이브리드 C 전략: 배치 LLM 평가 워커
- pending 상태의 후보 페어를 주기적으로 Claude로 평가
- 고득점 페어는 thought_pairs로 자동 이동
- 백그라운드 프로세스로 실행 가능

Usage:
    # 5분마다 100개씩 평가
    python backend/scripts/run_batch_worker.py --max-candidates 100 --interval 300

    # 백그라운드 실행
    nohup python backend/scripts/run_batch_worker.py > worker.log 2>&1 &

    # 한 번만 실행 (테스트용)
    python backend/scripts/run_batch_worker.py --max-candidates 100 --interval 0
"""

import asyncio
import argparse
import logging
import sys
from pathlib import Path

# Add backend to path
backend_path = str(Path(__file__).parent.parent)
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from services.supabase_service import SupabaseService
from services.ai_service import AIService
from services.batch_worker import BatchEvaluationWorker

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('batch_worker.log')
    ]
)
logger = logging.getLogger(__name__)


async def run_worker(max_candidates: int, interval: int):
    """
    배치 워커 실행.

    Args:
        max_candidates: 한 번에 처리할 최대 후보 개수
        interval: 실행 간격 (초), 0이면 한 번만 실행

    Process:
        1. 서비스 초기화 (Supabase, AI)
        2. BatchEvaluationWorker 생성
        3. 무한 루프 (interval > 0) 또는 한 번 실행 (interval = 0):
           - run_batch() 호출
           - 결과 로깅
           - sleep(interval)
    """
    logger.info(f"배치 워커 시작 (max={max_candidates}, interval={interval}s)")

    # 서비스 초기화
    supabase = SupabaseService()
    ai = AIService()

    # 워커 생성
    worker = BatchEvaluationWorker(
        supabase_service=supabase,
        ai_service=ai,
        batch_size=10,  # Claude 평가 배치 크기
        min_score_threshold=65,  # thought_pairs 이동 기준
        auto_migrate=True  # 고득점 자동 이동
    )

    # 실행 횟수 카운터
    run_count = 0

    while True:
        run_count += 1
        logger.info(f"=== 배치 실행 #{run_count} 시작 ===")

        try:
            result = await worker.run_batch(max_candidates=max_candidates)

            # 결과 로깅
            if result["evaluated"] == 0:
                logger.info("평가 대기 중인 후보가 없습니다. 대기 중...")
            else:
                logger.info(
                    f"배치 완료: "
                    f"평가 {result['evaluated']}개, "
                    f"이동 {result['migrated']}개, "
                    f"실패 {result['failed']}개"
                )

        except Exception as e:
            logger.error(f"배치 워커 에러: {e}", exc_info=True)

        # interval=0이면 한 번만 실행하고 종료
        if interval == 0:
            logger.info("단일 실행 모드로 종료")
            break

        # 다음 실행까지 대기
        logger.info(f"{interval}초 대기 중...")
        await asyncio.sleep(interval)


def main():
    """CLI 엔트리 포인트"""
    parser = argparse.ArgumentParser(
        description='하이브리드 C 전략: 배치 LLM 평가 워커',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  # 5분마다 100개씩 평가 (운영 모드)
  python backend/scripts/run_batch_worker.py --max-candidates 100 --interval 300

  # 10분마다 50개씩 평가 (낮은 부하)
  python backend/scripts/run_batch_worker.py --max-candidates 50 --interval 600

  # 한 번만 실행 (테스트 모드)
  python backend/scripts/run_batch_worker.py --max-candidates 100 --interval 0

  # 백그라운드 실행
  nohup python backend/scripts/run_batch_worker.py --max-candidates 100 --interval 300 > worker.log 2>&1 &

  # 백그라운드 프로세스 확인
  ps aux | grep run_batch_worker

  # 백그라운드 프로세스 종료
  pkill -f run_batch_worker
        """
    )

    parser.add_argument(
        '--max-candidates',
        type=int,
        default=100,
        help='한 번에 처리할 최대 후보 개수 (기본: 100)'
    )

    parser.add_argument(
        '--interval',
        type=int,
        default=300,
        help='실행 간격 (초). 0이면 한 번만 실행 (기본: 300초 = 5분)'
    )

    args = parser.parse_args()

    # 파라미터 검증
    if args.max_candidates <= 0:
        logger.error("max-candidates는 1 이상이어야 합니다.")
        sys.exit(1)

    if args.interval < 0:
        logger.error("interval은 0 이상이어야 합니다.")
        sys.exit(1)

    try:
        asyncio.run(run_worker(args.max_candidates, args.interval))
    except KeyboardInterrupt:
        logger.info("사용자가 워커를 중단했습니다 (Ctrl+C)")
    except Exception as e:
        logger.error(f"워커 실행 중 예상치 못한 에러: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
