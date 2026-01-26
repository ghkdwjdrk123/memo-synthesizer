#!/bin/bash
# Cron job for batch worker
#
# 설명: 5분마다 배치 워커를 실행하는 cron 스크립트
#
# Crontab 등록 방법:
#   1. crontab -e
#   2. 다음 라인 추가:
#      */5 * * * * /path/to/memo-synthesizer/backend/scripts/cron_batch_worker.sh
#
# 주의사항:
#   - PROJECT_DIR 경로를 실제 프로젝트 경로로 수정 필요
#   - Python 가상환경 경로도 확인 필요

# 프로젝트 디렉터리 설정 (실제 경로로 수정 필요)
PROJECT_DIR="/path/to/memo-synthesizer"
BACKEND_DIR="$PROJECT_DIR/backend"

# Python 가상환경 경로 (실제 경로로 수정 필요)
VENV_DIR="$PROJECT_DIR/venv"

# 로그 파일
LOG_FILE="$BACKEND_DIR/scripts/cron_batch_worker.log"

# 시작 시간 로깅
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Cron batch worker started" >> "$LOG_FILE"

# 프로젝트 디렉터리로 이동
cd "$BACKEND_DIR" || {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Failed to cd to $BACKEND_DIR" >> "$LOG_FILE"
    exit 1
}

# 가상환경 활성화
if [ -d "$VENV_DIR" ]; then
    source "$VENV_DIR/bin/activate"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Virtual environment not found at $VENV_DIR" >> "$LOG_FILE"
    exit 1
fi

# 배치 워커 실행 (한 번만 실행, interval=0)
python scripts/run_batch_worker.py \
    --max-candidates 100 \
    --interval 0 \
    >> "$LOG_FILE" 2>&1

# 종료 코드 확인
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Cron batch worker completed successfully" >> "$LOG_FILE"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Cron batch worker failed with exit code $EXIT_CODE" >> "$LOG_FILE"
fi

# 가상환경 비활성화
deactivate

exit $EXIT_CODE
