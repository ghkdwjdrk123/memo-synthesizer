"""
Pipeline 라우터.

RAW → NORMALIZED → ZK → Essay 전체 파이프라인 엔드포인트.
"""

import asyncio
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks

from schemas.raw import ImportResult, RawNoteCreate
from schemas.normalized import ThoughtUnitCreate
from schemas.zk import ThoughtPairCandidate, ThoughtPairCreate, PairScore
from schemas.essay import EssayCreate
from schemas.job import ImportJobCreate, ImportJobStartResponse, ImportJobStatus, ImportJobUpdate, FailedPage
from services.notion_service import NotionService
from services.ai_service import AIService, get_ai_service
from services.supabase_service import SupabaseService, get_supabase_service
from services.candidate_mining_service import CandidateMiningService, get_candidate_mining_service
from services.distribution_service import DistributionService, get_distribution_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


# ============================================================
# Helper Functions for Background Import
# ============================================================

async def _fetch_page_with_retry(
    notion_service: NotionService,
    page_id: str,
    max_retries: int = 3
) -> str:
    """
    Fetch page content with exponential backoff retry.

    Args:
        notion_service: Notion service instance
        page_id: Notion page ID
        max_retries: Maximum number of retry attempts

    Returns:
        str: Page content (empty string on total failure)
    """
    for attempt in range(max_retries):
        try:
            return await notion_service.fetch_page_blocks(page_id)
        except Exception as e:
            if attempt == max_retries - 1:
                # Last attempt failed, propagate error
                raise

            wait_time = 2 ** attempt  # 1s, 2s, 4s
            logger.warning(
                f"Retry {attempt+1}/{max_retries} for page {page_id}: {e}. "
                f"Waiting {wait_time}s..."
            )
            await asyncio.sleep(wait_time)

    return ""  # Should never reach here


async def _background_import_task(
    job_id: str,
    mode: str,
    page_size: int,
    notion_service: NotionService,
    supabase_service: SupabaseService
):
    """
    Background task for Notion import with content fetching.

    This RESTORES the commented-out content fetching loop (lines 85-101)
    but runs it in the background to avoid client timeout.

    Args:
        job_id: UUID of import job
        mode: Import mode ('database' or 'parent_page')
        page_size: Batch size for pagination
        notion_service: Notion service instance
        supabase_service: Supabase service instance
    """
    logger.info(f"[Job {job_id}] Starting background import (mode: {mode})")

    try:
        # Mark job as processing
        await supabase_service.update_import_job(
            job_id,
            ImportJobUpdate(status="processing", started_at=datetime.utcnow())
        )

        # Fetch pages based on mode
        from config import settings
        pages = []

        if mode == "database":
            query_result = await notion_service.query_database(page_size=page_size)
            if not query_result.get("success"):
                raise Exception(f"Notion query failed: {query_result.get('error')}")
            pages = query_result.get("pages", [])
        elif mode == "parent_page":
            pages = await notion_service.fetch_child_pages_from_parent(
                parent_page_id=settings.notion_parent_page_id,
                page_size=100
            )

        total_count = len(pages)
        logger.info(f"[Job {job_id}] Retrieved {total_count} pages")

        await supabase_service.update_import_job(
            job_id,
            ImportJobUpdate(total_pages=total_count)
        )

        # Incremental update: Detect changes
        new_page_ids, updated_page_ids, deleted_page_ids = await supabase_service.get_pages_to_fetch(pages)
        fetch_targets = set(new_page_ids + updated_page_ids)

        logger.info(
            f"[Job {job_id}] Incremental import: "
            f"{len(new_page_ids)} new, {len(updated_page_ids)} updated, "
            f"{len(deleted_page_ids)} deleted, "
            f"{len(pages) - len(fetch_targets)} unchanged (will skip)"
        )

        # Handle deleted pages (soft delete)
        if deleted_page_ids:
            logger.warning(
                f"[Job {job_id}] Found {len(deleted_page_ids)} pages deleted in Notion. "
                f"Marking as deleted (essays will be preserved)..."
            )
            for deleted_id in deleted_page_ids:
                try:
                    await supabase_service.soft_delete_raw_note(deleted_id)
                except Exception as e:
                    logger.error(f"[Job {job_id}] Failed to soft delete {deleted_id}: {e}")

        # Process each page with INCREMENTAL content fetching
        for idx, page in enumerate(pages, 1):
            page_id = page.get("id")

            try:
                # Skip unchanged pages
                if page_id not in fetch_targets:
                    logger.info(f"[Job {job_id}] [{idx}/{total_count}] ⏭️  Skipped (unchanged): {page_id}")
                    await supabase_service.increment_job_progress(job_id, skipped=True)
                    continue

                # Fetch block content (only for new/updated pages)
                fetched_content = None
                if mode == "parent_page":
                    try:
                        fetched_content = await _fetch_page_with_retry(notion_service, page_id, max_retries=3)
                        if "properties" not in page:
                            page["properties"] = {}

                        # If content is empty, use title as fallback
                        if not fetched_content or not fetched_content.strip():
                            # Extract title from properties
                            properties = page.get("properties", {})
                            title_value = None
                            for key in ["제목", "Name", "이름", "title"]:
                                if key in properties and properties[key]:
                                    title_value = properties[key]
                                    break

                            if title_value and isinstance(title_value, str):
                                fetched_content = title_value
                                page["properties"]["본문"] = title_value
                                logger.info(f"[Job {job_id}] [{idx}/{total_count}] ℹ Empty content, using title as fallback")
                            else:
                                fetched_content = ""
                                page["properties"]["본문"] = ""
                                logger.warning(f"[Job {job_id}] [{idx}/{total_count}] ⚠ No content and no title available")
                        else:
                            page["properties"]["본문"] = fetched_content
                            logger.info(f"[Job {job_id}] [{idx}/{total_count}] ✓ Fetched {len(fetched_content)} chars")
                    except Exception as e:
                        logger.warning(f"[Job {job_id}] [{idx}/{total_count}] ✗ Failed to fetch content: {e}")
                        fetched_content = ""
                        if "properties" not in page:
                            page["properties"] = {}
                        page["properties"]["본문"] = ""

                # Extract and upsert (same as original)
                properties = page.get("properties", {})
                title = None
                for key in ["제목", "Name", "이름", "title"]:
                    if key in properties and properties[key]:
                        title = properties[key]
                        break

                notion_url = page.get("url") or f"https://notion.so/{page_id.replace('-', '')}"

                # Use fetched_content directly instead of extracting from properties
                raw_note = RawNoteCreate(
                    notion_page_id=page_id,
                    notion_url=notion_url,
                    title=title if title and isinstance(title, str) else None,
                    content=fetched_content,
                    properties_json=properties,
                    notion_created_time=datetime.fromisoformat(
                        page.get("created_time").replace("Z", "+00:00")
                    ),
                    notion_last_edited_time=datetime.fromisoformat(
                        page.get("last_edited_time").replace("Z", "+00:00")
                    ),
                )

                await supabase_service.upsert_raw_note(raw_note)
                await supabase_service.increment_job_progress(job_id, imported=True)

            except Exception as e:
                logger.warning(f"[Job {job_id}] [{idx}/{total_count}] Failed: {str(e)}")
                await supabase_service.increment_job_progress(
                    job_id,
                    failed_page={"page_id": page_id, "error_message": str(e)[:500]}
                )

        # Calculate success rate and mark completed
        # Note: skipped 페이지도 성공으로 간주 (중복 방지는 의도된 동작)
        job = await supabase_service.get_import_job(job_id)
        total = job["total_pages"]
        imported = job["imported_pages"]
        skipped = job["skipped_pages"]
        success_count = imported + skipped  # 중복 방지(skip)도 성공
        success_rate = (success_count / total * 100) if total > 0 else 0

        if success_rate >= 90:
            status = "completed"
            message = f"Import completed: {imported} imported, {skipped} skipped (success rate: {success_rate:.1f}%)"
        else:
            status = "failed"
            message = f"Import failed: only {success_rate:.1f}% pages processed ({imported} imported, {skipped} skipped)"

        await supabase_service.update_import_job(
            job_id,
            ImportJobUpdate(
                status=status,
                error_message=message if status == "failed" else None,
                completed_at=datetime.utcnow()
            )
        )

        logger.info(
            f"[Job {job_id}] ✓ {status.upper()}: {imported}/{total} imported, "
            f"{skipped} skipped, {len(job.get('failed_pages', []))} failed"
        )

    except Exception as e:
        logger.error(f"[Job {job_id}] ✗ Critical failure: {str(e)}", exc_info=True)
        try:
            await supabase_service.update_import_job(
                job_id,
                ImportJobUpdate(
                    status="failed",
                    error_message=str(e)[:1000],
                    completed_at=datetime.utcnow()
                )
            )
        except Exception as update_error:
            logger.error(f"[Job {job_id}] Failed to update job status: {update_error}")


@router.post("/import-from-notion", response_model=ImportJobStartResponse)
async def import_from_notion(
    page_size: int = Query(default=100, ge=1, le=100, description="Batch size for pagination"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    supabase_service: SupabaseService = Depends(get_supabase_service),
):
    """
    Step 1: Start Notion import in background.

    Returns job_id immediately. Use GET /pipeline/import-status/{job_id} to check progress.

    Args:
        page_size: Batch size for pagination (1-100)
        background_tasks: FastAPI BackgroundTasks
        supabase_service: Supabase service (DI)

    Returns:
        ImportJobStartResponse: Job ID and status

    Process:
        1. Create import job record with 'pending' status
        2. Start background task to process import
        3. Background task:
           - Fetches child pages from Notion
           - Fetches content for each page (RESTORED)
           - Upserts to raw_notes table
           - Updates job progress in real-time
    """
    from config import settings

    try:
        # Determine mode
        if settings.notion_database_id:
            mode = "database"
        elif settings.notion_parent_page_id:
            mode = "parent_page"
        else:
            raise HTTPException(
                status_code=500,
                detail="Neither NOTION_DATABASE_ID nor NOTION_PARENT_PAGE_ID is configured"
            )

        # Create job record
        job_create = ImportJobCreate(
            mode=mode,
            config_json={"page_size": page_size, "timestamp": datetime.utcnow().isoformat()}
        )
        job = await supabase_service.create_import_job(job_create)
        job_id = str(job["id"])

        # Start background task
        notion_service = NotionService()
        background_tasks.add_task(
            _background_import_task,
            job_id=job_id,
            mode=mode,
            page_size=page_size,
            notion_service=notion_service,
            supabase_service=supabase_service
        )

        logger.info(f"Import job {job_id} started (mode: {mode})")

        return ImportJobStartResponse(
            job_id=job_id,
            status="pending",
            message=f"Import job started (mode: {mode}). Use GET /pipeline/import-status/{job_id} to check progress."
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start import job: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/import-status/{job_id}", response_model=ImportJobStatus)
async def get_import_status(
    job_id: str,
    supabase_service: SupabaseService = Depends(get_supabase_service),
):
    """
    Get import job status and progress.

    Poll this endpoint every 5 seconds until status is 'completed' or 'failed'.

    Args:
        job_id: UUID of import job
        supabase_service: Supabase service (DI)

    Returns:
        ImportJobStatus: Job status with progress information

    Raises:
        HTTPException: 404 if job not found, 500 on other errors
    """
    try:
        job = await supabase_service.get_import_job(job_id)

        # Calculate progress
        total = job.get("total_pages", 0)
        processed = job.get("processed_pages", 0)
        progress = (processed / total * 100) if total > 0 else 0.0

        # Calculate elapsed time
        elapsed_seconds = None
        if job.get("started_at"):
            from datetime import timezone
            start = datetime.fromisoformat(str(job["started_at"]).replace("Z", "+00:00"))
            end = (datetime.fromisoformat(str(job["completed_at"]).replace("Z", "+00:00"))
                   if job.get("completed_at") else datetime.now(timezone.utc))
            elapsed_seconds = (end - start).total_seconds()

        # Parse failed pages
        failed_pages = [
            FailedPage(page_id=fp["page_id"], error_message=fp["error_message"])
            for fp in job.get("failed_pages", [])
        ]

        return ImportJobStatus(
            job_id=job["id"],
            status=job["status"],
            mode=job["mode"],
            total_pages=job.get("total_pages", 0),
            processed_pages=job.get("processed_pages", 0),
            imported_pages=job.get("imported_pages", 0),
            skipped_pages=job.get("skipped_pages", 0),
            progress_percentage=round(progress, 1),
            created_at=job["created_at"],
            started_at=job.get("started_at"),
            completed_at=job.get("completed_at"),
            elapsed_seconds=round(elapsed_seconds, 1) if elapsed_seconds else None,
            error_message=job.get("error_message"),
            failed_pages=failed_pages,
            config_json=job.get("config_json", {})
        )

    except Exception as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=f"Import job {job_id} not found")
        logger.error(f"Failed to get import status for {job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/extract-thoughts")
async def extract_thoughts(
    supabase_service: SupabaseService = Depends(get_supabase_service),
    ai_service: AIService = Depends(get_ai_service),
):
    """
    Step 2: RAW 메모에서 사고 단위 추출 및 임베딩 생성.

    Args:
        supabase_service: Supabase 서비스 (DI)
        ai_service: AI 서비스 (DI)

    Returns:
        dict: 처리 결과 (성공/실패, 추출된 사고 단위 수)

    Process:
        1. raw_notes 테이블에서 모든 메모 조회
        2. 각 메모에 대해:
           a. Claude로 사고 단위 추출 (1-5개)
           b. OpenAI로 각 사고 단위의 임베딩 생성
           c. thought_units 테이블에 저장
    """
    result = {
        "success": False,
        "processed_notes": 0,
        "total_thoughts": 0,
        "errors": [],
    }

    try:
        # 1. 모든 raw_notes 조회
        logger.info("Fetching all raw notes...")
        raw_note_ids = await supabase_service.get_raw_note_ids()
        total_notes = len(raw_note_ids)

        if total_notes == 0:
            logger.warning("No raw notes found")
            result["success"] = True
            return result

        logger.info(f"Found {total_notes} raw notes to process")

        # 배치 처리 (10개씩)
        batch_size = 10
        processed = 0
        total_thoughts_extracted = 0

        for i in range(0, total_notes, batch_size):
            batch_ids = raw_note_ids[i : i + batch_size]
            notes_batch = await supabase_service.get_raw_notes_by_ids(batch_ids)

            logger.info(
                f"Processing batch {i//batch_size + 1}/{(total_notes + batch_size - 1)//batch_size} ({len(notes_batch)} notes)"
            )

            for note in notes_batch:
                try:
                    note_id = note["id"]
                    title = note.get("title", "")

                    # properties_json에서 본문 추출
                    props = note.get("properties_json", {})
                    content = props.get("본문", "") or props.get("content", "")

                    if not content and not title:
                        logger.warning(f"Note {note_id} has no title or content, skipping")
                        continue

                    # 2. Claude로 사고 단위 추출
                    logger.info(f"Extracting thoughts from note: {title[:50]}...")
                    extraction_result = await ai_service.extract_thoughts(
                        title=title or "", content=content or ""
                    )

                    thoughts_count = len(extraction_result.thoughts)
                    logger.info(f"Extracted {thoughts_count} thoughts")

                    # 3. 각 사고 단위에 대해 임베딩 생성
                    thought_units_to_insert = []

                    for thought in extraction_result.thoughts:
                        # 임베딩 생성 (claim + context 결합)
                        text_to_embed = thought.claim
                        if thought.context:
                            text_to_embed = f"{thought.claim}\n\n맥락: {thought.context}"

                        embedding_result = await ai_service.create_embedding(
                            text=text_to_embed, model="text-embedding-3-small"
                        )

                        if not embedding_result.get("success"):
                            logger.error(
                                f"Failed to create embedding: {embedding_result.get('error')}"
                            )
                            continue

                        # ThoughtUnitCreate 객체 생성
                        thought_unit = ThoughtUnitCreate(
                            raw_note_id=note_id,
                            claim=thought.claim,
                            context=thought.context,
                            embedding=embedding_result["embedding"],
                            embedding_model="text-embedding-3-small",
                        )
                        thought_units_to_insert.append(thought_unit)

                    # 4. 배치로 DB에 저장
                    if thought_units_to_insert:
                        inserted = await supabase_service.insert_thought_units_batch(
                            thought_units_to_insert
                        )
                        total_thoughts_extracted += len(inserted)
                        logger.info(
                            f"Inserted {len(inserted)} thought units for note {title[:50]}"
                        )

                    processed += 1

                except Exception as e:
                    logger.error(
                        f"Failed to process note {note.get('id')}: {e}", exc_info=True
                    )
                    result["errors"].append(
                        f"Note {note.get('id')}: {str(e)}"
                    )

        # 결과 반환
        result["success"] = True
        result["processed_notes"] = processed
        result["total_thoughts"] = total_thoughts_extracted

        logger.info(
            f"Thought extraction completed: {processed}/{total_notes} notes processed, "
            f"{total_thoughts_extracted} thoughts extracted"
        )

    except Exception as e:
        logger.error(f"Thought extraction failed: {e}", exc_info=True)
        result["errors"].append(str(e))
        raise HTTPException(status_code=500, detail=str(e))

    return result


@router.post("/select-pairs")
async def select_pairs(
    min_similarity: float = Query(default=0.05, ge=0, le=1, description="최소 유사도 (낮을수록 서로 다른 아이디어)"),
    max_similarity: float = Query(default=0.35, ge=0, le=1, description="최대 유사도"),
    top_k: int = Query(default=30, ge=10, le=100, description="각 thought당 검색할 상위 K개 (Top-K 알고리즘)"),
    min_score: int = Query(default=65, ge=0, le=100, description="최소 창의적 연결 점수 (threshold)"),
    supabase_service: SupabaseService = Depends(get_supabase_service),
    ai_service: AIService = Depends(get_ai_service),
):
    """
    Step 3: Top-K 알고리즘으로 낮은 유사도 범위 내 후보 쌍을 찾고, Claude로 평가하여 상위 N개를 DB에 저장.

    Args:
        min_similarity: 최소 유사도 (0-1, 기본 0.05, 낮을수록 서로 다른 아이디어)
        max_similarity: 최대 유사도 (0-1, 기본 0.35)
        top_k: 각 thought당 검색할 상위 K개 (10-100, 기본 30)
               - 클수록: 다양한 조합 발견, 느린 속도
               - 작을수록: 빠른 속도, 제한된 조합
        min_score: 최소 창의적 연결 점수 (0-100, 기본 65, threshold 필터링용)
                   - min_score 이상인 모든 페어를 저장
        supabase_service: Supabase 서비스 (DI)
        ai_service: AI 서비스 (DI)

    Returns:
        dict: 처리 결과 (후보 수, threshold 필터 후 개수, 선택된 페어 수, 페어 목록)

    Performance:
        - 복잡도: O(n × K) (기존 O(n²)에서 98% 개선)
        - 실행 시간: ~5초 (기존 60초+ 타임아웃)
        - HNSW 인덱스 자동 활용

    Process:
        1. min < max 검증
        2. find_candidate_pairs() 호출 (Top-K 알고리즘, HNSW 인덱스 활용)
        3. 후보 없으면 Fallback 전략 (범위 확대)
        4. ThoughtPairCandidate 객체 리스트 생성
        5. score_pairs() 호출 (Claude 평가)
        6. min_score 이상인 쌍만 필터링 (threshold)
        7. 점수 기준 정렬 (모든 합격 페어 선택)
        8. ThoughtPairCreate 객체 생성 (정렬 보장)
        9. insert_thought_pairs_batch() 호출
    """
    result = {
        "success": False,
        "candidates_found": 0,
        "candidates_after_threshold": 0,
        "pairs_selected": 0,
        "pairs": [],
        "errors": [],
    }

    try:
        # 1. 유사도 범위 검증
        if min_similarity >= max_similarity:
            raise HTTPException(
                status_code=400,
                detail="min_similarity must be less than max_similarity"
            )

        # 2. 후보 쌍 찾기 (Top-K 알고리즘, Fallback 전략 포함)
        logger.info(
            f"Finding candidate pairs (similarity: {min_similarity}-{max_similarity}, top_k={top_k})..."
        )
        candidates = await supabase_service.find_candidate_pairs(
            min_similarity=min_similarity,
            max_similarity=max_similarity,
            top_k=top_k
        )

        candidates_count = len(candidates)
        result["candidates_found"] = candidates_count

        # 3. 후보 없으면 Fallback 전략 (범위 확대)
        if candidates_count == 0:
            logger.warning(
                f"No candidates found in {min_similarity}-{max_similarity} range. "
                "Trying fallback strategies..."
            )

            # Fallback 1: 0.1-0.4 범위
            fallback_ranges = [
                (0.1, 0.4),
                (0.15, 0.45),
            ]

            for fb_min, fb_max in fallback_ranges:
                logger.info(f"Fallback: Trying range {fb_min}-{fb_max}...")
                candidates = await supabase_service.find_candidate_pairs(
                    min_similarity=fb_min,
                    max_similarity=fb_max,
                    top_k=top_k
                )
                if len(candidates) > 0:
                    candidates_count = len(candidates)
                    result["candidates_found"] = candidates_count
                    logger.info(f"Fallback successful: Found {candidates_count} candidates")
                    break

            # 모든 fallback 실패 시 에러
            if candidates_count == 0:
                raise HTTPException(
                    status_code=404,
                    detail=f"No candidate pairs found even after fallback strategies. "
                           f"Original range: {min_similarity}-{max_similarity}. "
                           f"Try adding more memos or adjusting the range manually."
                )

        logger.info(f"Found {candidates_count} candidate pairs from DIFFERENT sources")

        # 4. ThoughtPairCandidate 객체 생성
        pair_candidates = [
            ThoughtPairCandidate(
                thought_a_id=c["thought_a_id"],
                thought_b_id=c["thought_b_id"],
                thought_a_claim=c["thought_a_claim"],
                thought_b_claim=c["thought_b_claim"],
                similarity_score=c["similarity_score"]
            )
            for c in candidates
        ]

        # 5. Claude로 평가
        logger.info(f"Scoring {len(pair_candidates)} pairs with Claude...")
        scoring_result = await ai_service.score_pairs(pair_candidates)

        # scoring_result는 PairScoringResult Pydantic 객체
        pair_scores = scoring_result.pair_scores
        logger.info(f"Scored {len(pair_scores)} pairs")

        # 6. min_score 이상인 쌍만 필터링 (threshold)
        filtered_pairs = [
            pair for pair in pair_scores
            if pair.logical_expansion_score >= min_score
        ]
        result["candidates_after_threshold"] = len(filtered_pairs)

        logger.info(
            f"Filtered {len(filtered_pairs)}/{len(pair_scores)} pairs "
            f"with score >= {min_score}"
        )

        # 6-1. threshold 필터 후 0개이면 에러
        if len(filtered_pairs) == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No pairs passed the threshold (min_score={min_score}). "
                       f"All {len(pair_scores)} pairs scored below {min_score}. "
                       f"Try lowering min_score (e.g., {max(0, min_score - 10)}) or adding more memos."
            )

        # 7. 점수 기준 정렬 (모든 합격 페어 선택)
        sorted_pairs = sorted(
            filtered_pairs,
            key=lambda x: x.logical_expansion_score,
            reverse=True
        )
        selected_pairs = sorted_pairs  # top_n 제거: 모든 합격 페어 저장

        logger.info(f"Selected all {len(selected_pairs)} pairs that passed threshold")

        # 7. ThoughtPairCreate 객체 생성 (thought_a_id < thought_b_id 보장)
        pairs_to_insert = []
        for pair_score in selected_pairs:
            # 원본 후보에서 similarity_score 찾기
            original_candidate = next(
                (c for c in candidates
                 if (c["thought_a_id"] == pair_score.thought_a_id and
                     c["thought_b_id"] == pair_score.thought_b_id) or
                    (c["thought_a_id"] == pair_score.thought_b_id and
                     c["thought_b_id"] == pair_score.thought_a_id)),
                None
            )

            if not original_candidate:
                logger.warning(
                    f"Could not find original candidate for pair "
                    f"({pair_score.thought_a_id}, {pair_score.thought_b_id})"
                )
                continue

            # ID 정렬 보장
            thought_a_id = min(pair_score.thought_a_id, pair_score.thought_b_id)
            thought_b_id = max(pair_score.thought_a_id, pair_score.thought_b_id)

            pair_create = ThoughtPairCreate(
                thought_a_id=thought_a_id,
                thought_b_id=thought_b_id,
                similarity_score=original_candidate["similarity_score"],
                connection_reason=pair_score.connection_reason
            )
            pairs_to_insert.append(pair_create)

        # 8. DB에 저장
        if pairs_to_insert:
            logger.info(f"Inserting {len(pairs_to_insert)} pairs to DB...")
            inserted_pairs = await supabase_service.insert_thought_pairs_batch(
                pairs_to_insert
            )

            result["pairs_selected"] = len(inserted_pairs)

            # 응답용 페어 데이터 생성
            for inserted in inserted_pairs:
                pair_score = next(
                    (p for p in selected_pairs
                     if (p.thought_a_id == inserted["thought_a_id"] and
                         p.thought_b_id == inserted["thought_b_id"]) or
                        (p.thought_a_id == inserted["thought_b_id"] and
                         p.thought_b_id == inserted["thought_a_id"])),
                    None
                )

                result["pairs"].append({
                    "id": inserted["id"],
                    "thought_a_id": inserted["thought_a_id"],
                    "thought_b_id": inserted["thought_b_id"],
                    "similarity_score": inserted["similarity_score"],
                    "logical_expansion_score": pair_score.logical_expansion_score if pair_score else None,
                    "connection_reason": inserted["connection_reason"]
                })

            logger.info(f"Successfully inserted {len(inserted_pairs)} pairs")

        result["success"] = True

        logger.info(
            f"Pair selection completed: {candidates_count} candidates, "
            f"{result['pairs_selected']} selected"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Pair selection failed: {e}", exc_info=True)
        result["errors"].append(str(e))
        raise HTTPException(status_code=500, detail=str(e))

    return result


@router.get("/pairs")
async def get_pairs(
    only_unused: bool = Query(default=False, description="미사용 페어만 조회"),
    limit: int = Query(default=10, ge=1, le=100, description="최대 반환 개수"),
    supabase_service: SupabaseService = Depends(get_supabase_service),
):
    """
    저장된 페어 조회 (전체 또는 미사용만).

    Args:
        only_unused: True면 is_used_in_essay=FALSE만 조회
        limit: 최대 반환 개수 (1-100)
        supabase_service: Supabase 서비스 (DI)

    Returns:
        dict: 페어 목록 및 개수
    """
    result = {
        "success": False,
        "count": 0,
        "pairs": [],
        "errors": [],
    }

    try:
        logger.info(f"Fetching pairs (only_unused={only_unused}, limit={limit})...")

        if only_unused:
            # 미사용 페어만 조회
            pairs = await supabase_service.get_unused_thought_pairs(limit=limit)
        else:
            # 전체 조회 (similarity_score DESC)
            query = (
                supabase_service.client
                .table("thought_pairs")
                .select("*")
                .order("similarity_score", desc=True)
                .limit(limit)
            )
            response = await query.execute()
            pairs = response.data if response.data else []

        result["count"] = len(pairs)
        result["pairs"] = pairs
        result["success"] = True

        logger.info(f"Retrieved {len(pairs)} pairs")

    except Exception as e:
        logger.error(f"Failed to fetch pairs: {e}", exc_info=True)
        result["errors"].append(str(e))
        raise HTTPException(status_code=500, detail=str(e))

    return result


@router.post("/run-all")
async def run_all(
    page_size: int = Query(default=100, ge=1, le=100, description="Step 1용 메모 수"),
    min_similarity: float = Query(default=0.05, ge=0, le=1, description="Step 3용 최소 유사도 (낮은 값 = 서로 다른 아이디어)"),
    max_similarity: float = Query(default=0.35, ge=0, le=1, description="Step 3용 최대 유사도"),
    min_score: int = Query(default=65, ge=0, le=100, description="Step 3용 최소 창의적 연결 점수 (threshold)"),
    max_essay_pairs: int = Query(default=5, ge=1, le=10, description="Step 4용 에세이 생성할 페어 개수"),
    supabase_service: SupabaseService = Depends(get_supabase_service),
    ai_service: AIService = Depends(get_ai_service),
):
    """
    전체 파이프라인 (Step 1 → Step 2 → Step 3 → Step 4) 순차 실행.

    Args:
        page_size: Step 1에서 가져올 메모 수 (1-100)
        min_similarity: Step 3용 최소 유사도 (0-1, 기본 0.05 = 낮은 유사도)
        max_similarity: Step 3용 최대 유사도 (0-1, 기본 0.35)
        min_score: Step 3용 최소 창의적 연결 점수 (0-100, 기본 65, 이상인 모든 페어 저장)
        max_essay_pairs: Step 4용 에세이 생성할 페어 개수 (1-10)
        supabase_service: Supabase 서비스 (DI)
        ai_service: AI 서비스 (DI)

    Returns:
        dict: 각 단계별 결과
    """
    result = {
        "success": False,
        "step1": {},
        "step2": {},
        "step3": {},
        "step4": {},
        "errors": [],
    }

    try:
        logger.info("Starting full pipeline (Step 1 → Step 2 → Step 3 → Step 4)...")

        # Step 1: Notion에서 메모 가져오기
        logger.info("=== Step 1: Import from Notion ===")
        try:
            step1_result = await import_from_notion(
                page_size=page_size,
                supabase_service=supabase_service
            )
            result["step1"] = {
                "imported": step1_result.imported_count,
                "skipped": step1_result.skipped_count
            }
            if step1_result.errors:
                result["errors"].extend([f"Step1: {e}" for e in step1_result.errors])
            logger.info(
                f"Step 1 completed: {step1_result.imported_count} imported, "
                f"{step1_result.skipped_count} skipped"
            )
        except Exception as e:
            error_msg = f"Step 1 failed: {str(e)}"
            logger.error(error_msg)
            result["errors"].append(error_msg)
            result["step1"] = {"error": str(e)}

        # Step 2: 사고 단위 추출
        logger.info("=== Step 2: Extract Thoughts ===")
        try:
            step2_result = await extract_thoughts(
                supabase_service=supabase_service,
                ai_service=ai_service
            )
            result["step2"] = {"thoughts": step2_result.get("total_thoughts", 0)}
            if step2_result.get("errors"):
                result["errors"].extend([f"Step2: {e}" for e in step2_result["errors"]])
            logger.info(f"Step 2 completed: {step2_result.get('total_thoughts', 0)} thoughts extracted")
        except Exception as e:
            error_msg = f"Step 2 failed: {str(e)}"
            logger.error(error_msg)
            result["errors"].append(error_msg)
            result["step2"] = {"error": str(e)}

        # Step 3: 페어 선택
        logger.info("=== Step 3: Select Pairs ===")
        try:
            step3_result = await select_pairs(
                min_similarity=min_similarity,
                max_similarity=max_similarity,
                min_score=min_score,
                supabase_service=supabase_service,
                ai_service=ai_service
            )
            result["step3"] = {"pairs": step3_result.get("pairs_selected", 0)}
            if step3_result.get("errors"):
                result["errors"].extend([f"Step3: {e}" for e in step3_result["errors"]])
            logger.info(f"Step 3 completed: {step3_result.get('pairs_selected', 0)} pairs selected")
        except HTTPException as e:
            # 404 (후보 없음)는 치명적이지 않을 수 있음
            if e.status_code == 404:
                error_msg = f"Step 3 warning: {e.detail}"
                logger.warning(error_msg)
                result["errors"].append(error_msg)
                result["step3"] = {"pairs": 0, "warning": e.detail}
            else:
                raise
        except Exception as e:
            error_msg = f"Step 3 failed: {str(e)}"
            logger.error(error_msg)
            result["errors"].append(error_msg)
            result["step3"] = {"error": str(e)}

        # Step 4: 에세이 생성
        logger.info("=== Step 4: Generate Essays ===")
        try:
            step4_result = await generate_essays(
                max_pairs=max_essay_pairs,
                supabase_service=supabase_service,
                ai_service=ai_service
            )
            result["step4"] = {"essays": step4_result.get("essays_generated", 0)}
            if step4_result.get("errors"):
                result["errors"].extend([f"Step4: {e}" for e in step4_result["errors"]])
            logger.info(f"Step 4 completed: {step4_result.get('essays_generated', 0)} essays generated")
        except Exception as e:
            error_msg = f"Step 4 failed: {str(e)}"
            logger.error(error_msg)
            result["errors"].append(error_msg)
            result["step4"] = {"error": str(e)}

        # 성공 여부 판단 (적어도 Step 1이 성공했으면 부분 성공)
        result["success"] = result["step1"].get("imported", 0) > 0

        logger.info(
            f"Full pipeline completed: "
            f"Step1={result['step1']}, Step2={result['step2']}, "
            f"Step3={result['step3']}, Step4={result['step4']}"
        )

    except Exception as e:
        logger.error(f"Full pipeline failed: {e}", exc_info=True)
        result["errors"].append(str(e))
        raise HTTPException(status_code=500, detail=str(e))

    return result


@router.post("/generate-essays")
async def generate_essays(
    max_pairs: int = Query(default=5, ge=1, le=10, description="처리할 최대 페어 개수"),
    supabase_service: SupabaseService = Depends(get_supabase_service),
    ai_service: AIService = Depends(get_ai_service),
):
    """
    Step 4: Essay 글감 생성

    프로세스:
    1. get_unused_thought_pairs()로 미사용 페어 조회
    2. 각 페어에 대해:
       a. get_pair_with_thoughts()로 전체 정보 가져오기
       b. ai_service.generate_essay()로 Claude 호출
       c. 에세이 생성 (title, outline, reason)
    3. insert_essays_batch()로 배치 저장
    4. 각 페어의 is_used_in_essay = TRUE 업데이트
    5. 생성된 에세이 목록 반환

    Args:
        max_pairs: 처리할 최대 페어 개수 (기본 5, 최대 10)

    Returns:
        {
            "success": true,
            "pairs_processed": 5,
            "essays_generated": 5,
            "essays": [...],
            "errors": []
        }

    Note:
        - 부분 성공 허용: 일부 페어 실패해도 성공한 것은 저장
        - 각 페어는 독립적으로 처리 (한 페어 실패가 다른 페어에 영향 없음)
    """
    result = {
        "success": False,
        "pairs_processed": 0,
        "essays_generated": 0,
        "essays": [],
        "errors": [],
    }

    try:
        # 1. 미사용 페어 조회
        logger.info(f"Step 4: Fetching up to {max_pairs} unused pairs...")
        unused_pairs = await supabase_service.get_unused_thought_pairs(limit=max_pairs)

        if not unused_pairs:
            logger.warning("No unused pairs found")
            result["errors"].append("No unused pairs available. Run Step 3 first.")
            return result

        logger.info(f"Found {len(unused_pairs)} unused pairs")

        # 2. 각 페어에 대해 에세이 생성
        generated_essays: List[EssayCreate] = []
        processed_pair_ids: List[int] = []

        for pair in unused_pairs:
            pair_id = pair["id"]
            try:
                result["pairs_processed"] += 1

                # 2a. 페어 전체 정보 가져오기
                pair_data = await supabase_service.get_pair_with_thoughts(pair_id)

                # 2b. Claude로 에세이 생성
                logger.info(f"Generating essay for pair {pair_id}...")
                essay_dict = await ai_service.generate_essay(pair_data)

                # 2c. EssayCreate 모델 생성
                essay = EssayCreate(
                    title=essay_dict["title"],
                    outline=essay_dict["outline"],
                    used_thoughts=essay_dict["used_thoughts"],
                    reason=essay_dict["reason"],
                    pair_id=pair_id
                )

                generated_essays.append(essay)
                processed_pair_ids.append(pair_id)
                logger.info(f"✓ Essay generated for pair {pair_id}: {essay.title[:50]}...")

            except Exception as e:
                error_msg = f"Failed to generate essay for pair {pair_id}: {str(e)}"
                logger.error(error_msg, exc_info=True)
                result["errors"].append(error_msg)
                # 계속 진행 (부분 성공 허용)

        # 3. 생성된 에세이 배치 저장
        if generated_essays:
            logger.info(f"Saving {len(generated_essays)} essays to DB...")
            saved_essays = await supabase_service.insert_essays_batch(generated_essays)
            result["essays_generated"] = len(saved_essays)
            result["essays"] = saved_essays

            # 4. 사용된 페어 상태 업데이트
            logger.info("Updating pair usage status...")
            for pair_id in processed_pair_ids:
                try:
                    await supabase_service.update_pair_used_status(pair_id, is_used=True)
                except Exception as e:
                    logger.error(f"Failed to update pair {pair_id} status: {e}")
                    # 에러 무시 (에세이는 이미 저장됨)

            logger.info(f"✓ Step 4 completed: {len(saved_essays)} essays generated")
            result["success"] = True
        else:
            logger.warning("No essays were successfully generated")
            result["errors"].append("All essay generation attempts failed")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Step 4 failed: {e}", exc_info=True)
        result["errors"].append(f"Pipeline error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

    return result


@router.get("/essays")
async def get_essays_list(
    limit: int = Query(default=10, ge=1, le=100, description="최대 반환 개수"),
    offset: int = Query(default=0, ge=0, description="건너뛸 개수"),
    supabase_service: SupabaseService = Depends(get_supabase_service),
):
    """
    저장된 에세이 목록 조회 (최신순).

    Args:
        limit: 최대 반환 개수 (기본 10)
        offset: 건너뛸 개수 (페이지네이션)

    Returns:
        {
            "total": int,
            "essays": [...]
        }
    """
    try:
        essays = await supabase_service.get_essays(limit=limit, offset=offset)

        # TODO: total count 쿼리 추가 (현재는 반환된 개수로 대체)
        return {
            "total": len(essays),
            "essays": essays
        }

    except Exception as e:
        logger.error(f"Failed to get essays: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 하이브리드 C 전략 엔드포인트
# ============================================================


@router.get("/distribution")
async def get_similarity_distribution(
    force_recalculate: bool = Query(default=False, description="강제 재계산 여부"),
    supabase_service: SupabaseService = Depends(get_supabase_service),
):
    """
    유사도 분포 조회 및 관리.

    워크플로우:
        1. DistributionService 초기화
        2. get_distribution() 호출 (캐시 or 계산)
        3. 각 전략별 임계값 미리 계산 (프리뷰)
        4. 결과 반환

    Args:
        force_recalculate: 강제 재계산 여부 (기본 False, 캐시 활용)
        supabase_service: Supabase 서비스 (DI)

    Returns:
        {
            "thought_count": 1921,
            "total_pairs": 38420,
            "percentiles": {
                "p0": 0.26, "p10": 0.30, "p20": 0.32,
                "p30": 0.34, "p40": 0.36, "p50": 0.38,
                "p60": 0.40, "p70": 0.42, "p80": 0.44,
                "p90": 0.46, "p100": 0.50
            },
            "mean": 0.38,
            "stddev": 0.05,
            "calculated_at": "2026-01-26T10:00:00",
            "duration_ms": 5432,
            "strategies": {
                "p30_p60": [0.34, 0.40],
                "p10_p40": [0.30, 0.36],
                "p0_p30": [0.26, 0.34]
            }
        }

    Example:
        >>> GET /pipeline/distribution  # 캐시 조회
        >>> GET /pipeline/distribution?force_recalculate=true  # 강제 재계산

    Note:
        - 분포 계산은 모든 thought_units의 유사도 페어를 분석합니다.
        - 첫 계산 시 ~5-10초 소요, 이후 캐시 활용 시 <0.1초
        - strategies는 각 전략별 임계값 범위를 프리뷰로 제공합니다.
    """
    try:
        # 1. DistributionService 초기화
        from services.distribution_service import DistributionService
        dist_service = DistributionService(supabase_service)

        # 2. 분포 조회
        logger.info(f"Getting distribution (force_recalculate={force_recalculate})...")
        dist = await dist_service.get_distribution(force_recalculate=force_recalculate)

        # 3. 각 전략별 임계값 미리 계산 (프리뷰)
        strategies_preview = {}
        for strategy_name in ["p30_p60", "p10_p40", "p0_p30"]:
            min_sim, max_sim = await dist_service.get_relative_thresholds(
                strategy=strategy_name
            )
            strategies_preview[strategy_name] = [
                round(min_sim, 3),
                round(max_sim, 3)
            ]

        # 4. 결과 반환
        return {
            **dist,
            "strategies": strategies_preview
        }

    except Exception as e:
        logger.error(f"Failed to get distribution: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/collect-candidates")
async def collect_candidates(
    strategy: str = Query(default="p10_p40", description="Percentile strategy: p10_p40, p30_p60, p0_p30, custom"),
    custom_min_pct: Optional[int] = Query(default=None, ge=0, le=100, description="Custom 최소 백분위수 (strategy=custom일 때)"),
    custom_max_pct: Optional[int] = Query(default=None, ge=0, le=100, description="Custom 최대 백분위수 (strategy=custom일 때)"),
    top_k: int = Query(default=20, ge=10, le=100, description="각 thought당 검색할 상위 K개"),
    use_distance_table: bool = Query(default=True, description="Distance Table 사용 여부 (기본값 True)"),
    supabase_service: SupabaseService = Depends(get_supabase_service),
    dist_service = Depends(lambda: None),  # DI 설정 필요 (아래에서 초기화)
):
    """
    전체 후보 수집 및 pair_candidates 테이블 저장 (상대적 임계값 기반).

    워크플로우:
        1. get_relative_thresholds() 호출 (상대적 임계값 계산)
        2. Distance Table 사용 여부에 따라 분기:
           - use_distance_table=True: get_candidates_from_distance_table() (0.1초)
           - use_distance_table=False: find_candidate_pairs() RPC (60초+)
        3. thought_units에서 raw_note_id JOIN (Distance Table 사용 시는 이미 포함)
        4. PairCandidateCreate 객체 생성
        5. insert_pair_candidates_batch() 호출

    Args:
        strategy: 백분위수 전략
            - "p10_p40": 하위 10-40% 구간 (기본, 창의적 조합)
            - "p30_p60": 하위 30-60% 구간 (안전한 연결)
            - "p0_p30": 최하위 30% (매우 다른 아이디어)
            - "custom": custom_min_pct, custom_max_pct 사용
        custom_min_pct: Custom 최소 백분위수 (strategy=custom일 때)
        custom_max_pct: Custom 최대 백분위수 (strategy=custom일 때)
        top_k: 각 thought당 검색할 상위 K개 (10-100, 기본 20)
        use_distance_table: Distance Table 사용 여부 (기본 True)
            - True: 초고속 조회 (0.1초, 권장)
            - False: v4 fallback (60초+, 안정성 검증용)
        supabase_service: Supabase 서비스 (DI)
        dist_service: Distribution 서비스 (DI)

    Returns:
        {
            "success": bool,
            "strategy": str,
            "min_similarity": float,
            "max_similarity": float,
            "total_candidates": int,
            "inserted": int,
            "duplicates": int,
            "query_method": str,  # "distance_table" or "v4_fallback"
            "errors": []
        }

    Raises:
        HTTPException(400): 유사도 범위가 80%를 초과하는 경우
            - Example: custom_min_pct=0, custom_max_pct=100 (100% 범위)
            - 정상 범위: 30-40% (p10_p40, p30_p60 등)
            - 메시지: "Invalid similarity range. Please use standard strategies..."
        HTTPException(500): DB 조회 실패, RPC 에러 등 서버 내부 오류

    Example:
        >>> POST /pipeline/collect-candidates?strategy=p10_p40&use_distance_table=true
        >>> {
        >>>     "success": true,
        >>>     "strategy": "p10_p40",
        >>>     "min_similarity": 0.28,
        >>>     "max_similarity": 0.34,
        >>>     "total_candidates": 30000,
        >>>     "inserted": 28500,
        >>>     "duplicates": 1500,
        >>>     "query_method": "distance_table"
        >>> }
    """
    result = {
        "success": False,
        "strategy": strategy,
        "min_similarity": 0.0,
        "max_similarity": 0.0,
        "total_candidates": 0,
        "inserted": 0,
        "duplicates": 0,
        "query_method": "",
        "errors": [],
    }

    try:
        # DistributionService 초기화
        from services.distribution_service import DistributionService
        dist_service = DistributionService(supabase_service)

        # 1. 상대적 임계값 계산
        logger.info(f"Calculating relative thresholds (strategy={strategy})...")
        min_similarity, max_similarity = await dist_service.get_relative_thresholds(
            strategy=strategy,
            custom_range=(custom_min_pct, custom_max_pct) if strategy == "custom" else None
        )

        result["min_similarity"] = min_similarity
        result["max_similarity"] = max_similarity

        logger.info(
            f"Collecting candidate pairs (similarity: {min_similarity:.3f}-{max_similarity:.3f}, "
            f"use_distance_table={use_distance_table})..."
        )

        # 2. 후보 수집 (Distance Table vs v4 fallback)
        if use_distance_table:
            # Distance Table 사용 (초고속, 권장, 무제한)
            logger.info("Using Distance Table (instant query, <0.1s, no limit)...")
            try:
                candidates = await supabase_service.get_candidates_from_distance_table(
                    min_similarity=min_similarity,
                    max_similarity=max_similarity
                )
                result["query_method"] = "distance_table"

                logger.info(f"Retrieved {len(candidates)} candidates from Distance Table")
            except ValueError as e:
                # 범위 검증 실패 (80% 초과)
                error_details = str(e)
                logger.error(f"Range validation failed: {error_details}")
                # 사용자에게는 간단한 메시지만 전달 (내부 구조 노출 방지)
                raise HTTPException(
                    status_code=400,
                    detail="Invalid similarity range. Please use standard strategies (p10_p40, p30_p60, p0_p30) or reduce custom range to 80% or less."
                )
        else:
            # v4 fallback (느림, 60초+)
            logger.warning("Using v4 fallback (slow, 60s+)...")

            # 후보 쌍 찾기 (Top-K 알고리즘, 필터링 없음)
            all_candidates = await supabase_service.find_candidate_pairs(
                min_similarity=0.0,  # SQL 필터링 비활성화
                max_similarity=1.0,  # SQL 필터링 비활성화
                top_k=top_k,
                limit=50000  # 최대 5만 개까지 수집
            )

            logger.info(f"Retrieved {len(all_candidates)} candidates from SQL (before filtering)")

            # Python 레벨에서 유사도 범위 필터링 (성능 최적화)
            candidates = [
                c for c in all_candidates
                if min_similarity <= c["similarity_score"] <= max_similarity
            ]

            result["query_method"] = "v4_fallback"

            logger.info(
                f"Filtered to {len(candidates)} candidates "
                f"(similarity: {min_similarity:.3f}-{max_similarity:.3f})"
            )

        total_count = len(candidates)
        result["total_candidates"] = total_count

        if total_count == 0:
            logger.warning(
                f"No candidates in range {min_similarity:.3f}-{max_similarity:.3f}"
            )
            result["success"] = True
            return result

        # 4. PairCandidateCreate 객체 생성
        from schemas.zk import PairCandidateCreate

        # Distance Table은 similarity 키 사용, v4는 similarity_score 키 사용
        similarity_key = "similarity" if use_distance_table else "similarity_score"

        pair_candidates_to_insert = [
            PairCandidateCreate(
                thought_a_id=c["thought_a_id"],
                thought_b_id=c["thought_b_id"],
                similarity=c[similarity_key],
                raw_note_id_a=c["raw_note_id_a"],
                raw_note_id_b=c["raw_note_id_b"]
            )
            for c in candidates
        ]

        # 5. 배치 저장
        logger.info(f"Inserting {len(pair_candidates_to_insert)} candidates to pair_candidates table...")
        batch_result = await supabase_service.insert_pair_candidates_batch(
            pair_candidates_to_insert
        )

        result["inserted"] = batch_result.inserted_count
        result["duplicates"] = batch_result.duplicate_count
        result["success"] = True

        logger.info(
            f"Candidate collection completed: {batch_result.inserted_count} inserted, "
            f"{batch_result.duplicate_count} duplicates"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Candidate collection failed: {e}", exc_info=True)
        result["errors"].append(str(e))
        raise HTTPException(status_code=500, detail=str(e))

    return result


@router.post("/sample-initial")
async def sample_initial(
    sample_size: int = Query(default=100, ge=10, le=200, description="샘플링할 후보 개수"),
    strategy: str = Query(default="p10_p40", description="Percentile strategy: p10_p40, p30_p60, p0_p30, custom"),
    custom_min_pct: Optional[int] = Query(default=None, ge=0, le=100, description="Custom 최소 백분위수 (strategy=custom일 때)"),
    custom_max_pct: Optional[int] = Query(default=None, ge=0, le=100, description="Custom 최대 백분위수 (strategy=custom일 때)"),
    supabase_service: SupabaseService = Depends(get_supabase_service),
    ai_service: AIService = Depends(get_ai_service),
):
    """
    초기 샘플 평가 (상대적 임계값 기반).

    워크플로우:
        1. get_relative_thresholds() 호출 (상대적 임계값 계산)
        2. get_pending_candidates() 호출 (최대 50000개)
        3. SamplingStrategy.sample_initial() 호출 (샘플 선택)
        4. BatchEvaluationWorker.run_batch() 호출 (평가 + 이동)

    Args:
        sample_size: 샘플링할 후보 개수 (10-200, 기본 100)
        strategy: 백분위수 전략
            - "p10_p40": 하위 10-40% 구간 (기본, 창의적 조합)
            - "p30_p60": 하위 30-60% 구간 (안전한 연결)
            - "p0_p30": 최하위 30% (매우 다른 아이디어)
            - "custom": custom_min_pct, custom_max_pct 사용
        custom_min_pct: Custom 최소 백분위수 (strategy=custom일 때)
        custom_max_pct: Custom 최대 백분위수 (strategy=custom일 때)
        supabase_service: Supabase 서비스 (DI)
        ai_service: AI 서비스 (DI)

    Returns:
        {
            "success": bool,
            "strategy": str,
            "min_similarity": float,
            "max_similarity": float,
            "sampled": int,
            "evaluated": int,
            "migrated": int,
            "errors": []
        }

    Example:
        >>> POST /pipeline/sample-initial?sample_size=100&strategy=p10_p40
        >>> {
        >>>     "success": true,
        >>>     "strategy": "p10_p40",
        >>>     "min_similarity": 0.28,
        >>>     "max_similarity": 0.34,
        >>>     "sampled": 100,
        >>>     "evaluated": 98,
        >>>     "migrated": 45
        >>> }
    """
    result = {
        "success": False,
        "strategy": strategy,
        "min_similarity": 0.0,
        "max_similarity": 0.0,
        "sampled": 0,
        "evaluated": 0,
        "migrated": 0,
        "errors": [],
    }

    try:
        # DistributionService 초기화
        from services.distribution_service import DistributionService
        dist_service = DistributionService(supabase_service)

        # 1. 상대적 임계값 계산
        logger.info(f"Calculating relative thresholds (strategy={strategy})...")
        min_similarity, max_similarity = await dist_service.get_relative_thresholds(
            strategy=strategy,
            custom_range=(custom_min_pct, custom_max_pct) if strategy == "custom" else None
        )

        result["min_similarity"] = min_similarity
        result["max_similarity"] = max_similarity

        logger.info(f"Starting initial sampling (sample_size={sample_size})...")

        # 2. pending 후보 조회
        logger.info("Fetching pending candidates...")
        pending_candidates = await supabase_service.get_pending_candidates(limit=50000)

        if not pending_candidates:
            logger.warning("No pending candidates found")
            result["errors"].append("No pending candidates available. Run /collect-candidates first.")
            return result

        logger.info(f"Found {len(pending_candidates)} pending candidates")

        # 3. 샘플링
        from services.sampling import SamplingStrategy

        sampling_strategy = SamplingStrategy(distribution_service=dist_service)
        sampled = await sampling_strategy.sample_initial(
            candidates=pending_candidates,
            target_count=sample_size
        )

        result["sampled"] = len(sampled)
        logger.info(f"Sampled {len(sampled)} candidates")

        if not sampled:
            logger.warning("Sampling returned empty result")
            result["errors"].append("Sampling failed to select candidates")
            return result

        # 4. 배치 평가
        from services.batch_worker import BatchEvaluationWorker

        worker = BatchEvaluationWorker(
            supabase_service=supabase_service,
            ai_service=ai_service,
            batch_size=10,
            min_score_threshold=65,
            auto_migrate=True
        )

        # 샘플링된 후보의 ID만 추출하여 처리
        # (BatchEvaluationWorker는 get_pending_candidates를 다시 호출하므로,
        # 샘플링된 후보만 처리하려면 상태를 변경해야 하지만,
        # 현재는 샘플링된 개수만큼만 처리)
        batch_result = await worker.run_batch(max_candidates=len(sampled))

        result["evaluated"] = batch_result.get("evaluated", 0)
        result["migrated"] = batch_result.get("migrated", 0)
        result["success"] = True

        logger.info(
            f"Initial sampling completed: {result['sampled']} sampled, "
            f"{result['evaluated']} evaluated, {result['migrated']} migrated"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Initial sampling failed: {e}", exc_info=True)
        result["errors"].append(str(e))
        raise HTTPException(status_code=500, detail=str(e))

    return result


@router.post("/score-candidates")
async def score_candidates(
    max_candidates: int = Query(default=100, ge=1, le=500, description="최대 평가 후보 개수"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    supabase_service: SupabaseService = Depends(get_supabase_service),
    ai_service: AIService = Depends(get_ai_service),
):
    """
    배치 평가 (백그라운드).

    워크플로우:
        1. BatchEvaluationWorker 초기화
        2. BackgroundTasks에 run_batch() 추가
        3. 즉시 응답 반환 (백그라운드 실행)

    Args:
        max_candidates: 최대 평가 후보 개수 (1-500, 기본 100)
        background_tasks: FastAPI BackgroundTasks (DI)
        supabase_service: Supabase 서비스 (DI)
        ai_service: AI 서비스 (DI)

    Returns:
        {
            "success": bool,
            "message": str
        }

    Example:
        >>> POST /pipeline/score-candidates?max_candidates=100
        >>> {
        >>>     "success": true,
        >>>     "message": "Batch evaluation started (max 100 candidates)"
        >>> }

    Note:
        - 이 엔드포인트는 즉시 응답을 반환하고 백그라운드에서 처리합니다.
        - 처리 결과는 pair_candidates 테이블의 llm_status, llm_score를 확인하세요.
        - 고득점(>= 65) 후보는 자동으로 thought_pairs 테이블로 이동됩니다.
    """
    try:
        logger.info(f"Starting background batch evaluation (max_candidates={max_candidates})...")

        from services.batch_worker import BatchEvaluationWorker

        # 워커 초기화
        worker = BatchEvaluationWorker(
            supabase_service=supabase_service,
            ai_service=ai_service,
            batch_size=10,
            min_score_threshold=65,
            auto_migrate=True
        )

        # 백그라운드 태스크 추가
        background_tasks.add_task(
            worker.run_batch,
            max_candidates=max_candidates
        )

        return {
            "success": True,
            "message": f"Batch evaluation started (max {max_candidates} candidates)"
        }

    except Exception as e:
        logger.error(f"Failed to start batch evaluation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/essays/recommended")
async def get_recommended_essays(
    limit: int = Query(default=10, ge=1, le=50, description="반환할 추천 개수"),
    quality_tiers: List[str] = Query(
        default=["excellent", "premium", "standard"],
        description="우선순위 quality tier 목록"
    ),
    supabase_service: SupabaseService = Depends(get_supabase_service),
):
    """
    AI 추천 Essay 후보 조회.

    워크플로우:
        1. RecommendationEngine.get_recommended_pairs() 호출
        2. quality_tier + 다양성 기반 추천 알고리즘 적용
        3. 상위 N개 반환

    Args:
        limit: 반환할 추천 개수 (1-50, 기본 10)
        quality_tiers: 우선순위 tier 목록 (기본: ["excellent", "premium", "standard"])
                       - excellent: 95-100점
                       - premium: 85-94점
                       - standard: 65-84점
        supabase_service: Supabase 서비스 (DI)

    Returns:
        {
            "total": int,
            "pairs": [
                {
                    "id": int,
                    "thought_a_id": int,
                    "thought_b_id": int,
                    "similarity_score": float,
                    "connection_reason": str,
                    "claude_score": int,
                    "quality_tier": str,
                    "diversity_score": float,
                    "final_score": float
                },
                ...
            ]
        }

    Example:
        >>> GET /essays/recommended?limit=5&quality_tiers=excellent&quality_tiers=premium
        >>> {
        >>>     "total": 5,
        >>>     "pairs": [...]
        >>> }

    Note:
        - 다양성 가중치는 0.3으로 고정 (claude_score 70% + diversity 30%)
        - 동일 raw_note가 자주 등장하면 diversity_score가 낮아짐
        - final_score = claude_score × 0.7 + (diversity_score × 100) × 0.3
    """
    result = {
        "total": 0,
        "pairs": [],
    }

    try:
        logger.info(
            f"Getting recommended essays (limit={limit}, tiers={quality_tiers})..."
        )

        from services.recommendation import RecommendationEngine

        # 추천 엔진 초기화
        engine = RecommendationEngine(supabase_service=supabase_service)

        # 추천 페어 조회
        recommended_pairs = await engine.get_recommended_pairs(
            limit=limit,
            quality_tiers=quality_tiers,
            diversity_weight=0.3
        )

        result["total"] = len(recommended_pairs)
        result["pairs"] = recommended_pairs

        logger.info(f"Returned {len(recommended_pairs)} recommended pairs")

    except Exception as e:
        logger.error(f"Failed to get recommended essays: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

    return result


# ============================================================
# 샘플링 기반 마이닝 엔드포인트 (신규)
# ============================================================


@router.post("/mine-candidates/batch")
async def mine_candidates_batch(
    last_src_id: int = Query(default=0, ge=0, description="마지막 처리한 src ID (키셋 페이징)"),
    src_batch: int = Query(default=30, ge=10, le=100, description="배치당 src 수"),
    dst_sample: int = Query(default=1200, ge=500, le=3000, description="dst 샘플 크기"),
    k: int = Query(default=15, ge=5, le=30, description="src당 후보 수"),
    p_lo: float = Query(default=0.10, ge=0.0, le=0.5, description="하위 분위수"),
    p_hi: float = Query(default=0.35, ge=0.1, le=1.0, description="상위 분위수"),
    seed: int = Query(default=42, description="결정론적 샘플링용 시드"),
    max_rounds: int = Query(default=3, ge=1, le=5, description="최대 재시도 횟수"),
    mining_service: CandidateMiningService = Depends(get_candidate_mining_service),
):
    """
    단일 배치 마이닝 실행.

    샘플링 기반으로 src당 k개의 후보 페어를 생성합니다.
    키셋 페이징을 사용하여 대용량 데이터셋에서도 안정적으로 동작합니다.

    Args:
        last_src_id: 마지막 처리한 src ID (키셋 페이징, 0부터 시작)
        src_batch: 배치당 처리할 src 수 (기본 30)
        dst_sample: dst 샘플 크기 (기본 1200)
        k: src당 생성할 후보 수 (기본 15)
        p_lo: 하위 분위수 (기본 0.10)
        p_hi: 상위 분위수 (기본 0.35)
        seed: 결정론적 샘플링용 시드 (기본 42)
        max_rounds: 최대 재시도 횟수 (기본 3)

    Returns:
        {
            "success": bool,
            "new_last_src_id": int,  # 다음 배치의 시작점
            "inserted_count": int,
            "src_processed_count": int,
            "rounds_used": int,
            "band_lo": float,
            "band_hi": float,
            "avg_candidates_per_src": float,
            "duration_ms": int
        }

    Example:
        # 첫 번째 배치
        POST /pipeline/mine-candidates/batch?last_src_id=0

        # 두 번째 배치 (이전 응답의 new_last_src_id 사용)
        POST /pipeline/mine-candidates/batch?last_src_id=30
    """
    try:
        result = await mining_service.mine_batch(
            last_src_id=last_src_id,
            src_batch=src_batch,
            dst_sample=dst_sample,
            k=k,
            p_lo=p_lo,
            p_hi=p_hi,
            seed=seed,
            max_rounds=max_rounds
        )

        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Mine batch failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mine-candidates/full")
async def mine_candidates_full(
    src_batch: int = Query(default=30, ge=10, le=100, description="배치당 src 수"),
    dst_sample: int = Query(default=1200, ge=500, le=3000, description="dst 샘플 크기"),
    k: int = Query(default=15, ge=5, le=30, description="src당 후보 수"),
    p_lo: float = Query(default=0.10, ge=0.0, le=0.5, description="하위 분위수"),
    p_hi: float = Query(default=0.35, ge=0.1, le=1.0, description="상위 분위수"),
    seed: int = Query(default=42, description="결정론적 샘플링용 시드"),
    max_rounds: int = Query(default=3, ge=1, le=5, description="최대 재시도 횟수"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    mining_service: CandidateMiningService = Depends(get_candidate_mining_service),
):
    """
    전체 마이닝 실행 (백그라운드).

    모든 thought에 대해 마이닝을 실행합니다.
    백그라운드로 실행되며 진행 상태는 GET /pipeline/mine-candidates/progress로 확인합니다.

    Args:
        src_batch: 배치당 처리할 src 수 (기본 30)
        dst_sample: dst 샘플 크기 (기본 1200)
        k: src당 생성할 후보 수 (기본 15)
        p_lo: 하위 분위수 (기본 0.10)
        p_hi: 상위 분위수 (기본 0.35)
        seed: 결정론적 샘플링용 시드 (기본 42)
        max_rounds: 최대 재시도 횟수 (기본 3)

    Returns:
        {
            "success": true,
            "message": "Full mining started in background"
        }

    Example:
        POST /pipeline/mine-candidates/full
    """
    try:
        logger.info("Starting full mining in background...")

        # 백그라운드 태스크 추가
        background_tasks.add_task(
            mining_service.mine_full,
            src_batch=src_batch,
            dst_sample=dst_sample,
            k=k,
            p_lo=p_lo,
            p_hi=p_hi,
            seed=seed,
            max_rounds=max_rounds
        )

        return {
            "success": True,
            "message": "Full mining started in background. Check /pipeline/mine-candidates/progress for status."
        }

    except Exception as e:
        logger.error(f"Failed to start full mining: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mine-candidates/progress")
async def get_mining_progress(
    mining_service: CandidateMiningService = Depends(get_candidate_mining_service),
):
    """
    마이닝 진행 상태 조회.

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

    Example:
        GET /pipeline/mine-candidates/progress
    """
    try:
        progress = await mining_service.get_progress()

        if not progress:
            return {
                "status": "no_progress",
                "message": "No mining progress found. Run /pipeline/mine-candidates/full first."
            }

        return progress

    except Exception as e:
        logger.error(f"Failed to get mining progress: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 분포 스케치 엔드포인트 (신규)
# ============================================================


@router.post("/distribution/sketch/build")
async def build_distribution_sketch(
    seed: int = Query(default=42, description="결정론적 샘플링용 시드"),
    src_sample: int = Query(default=200, ge=50, le=500, description="src 샘플 크기"),
    dst_sample: int = Query(default=500, ge=100, le=1000, description="dst 샘플 크기"),
    rounds: int = Query(default=1, ge=1, le=10, description="샘플링 라운드 수"),
    exclude_same_memo: bool = Query(default=True, description="같은 메모 제외 여부"),
    dist_service: DistributionService = Depends(get_distribution_service),
):
    """
    전역 분포 스케치 빌드 (샘플 수집).

    랜덤 샘플을 수집하여 전역 유사도 분포를 근사합니다.
    권장: src=200, dst=500, rounds=1 → 10만 샘플

    Args:
        seed: 결정론적 샘플링용 시드
        src_sample: src 샘플 크기 (기본 200)
        dst_sample: dst 샘플 크기 (기본 500)
        rounds: 샘플링 라운드 수 (기본 1)
        exclude_same_memo: 같은 메모 제외 여부 (기본 True)

    Returns:
        {
            "success": bool,
            "run_id": str,
            "inserted_samples": int,
            "total_thoughts": int,
            "coverage_estimate": float,
            "duration_ms": int
        }

    Example:
        POST /pipeline/distribution/sketch/build
    """
    try:
        result = await dist_service.build_sketch(
            seed=seed,
            src_sample=src_sample,
            dst_sample=dst_sample,
            rounds=rounds,
            exclude_same_memo=exclude_same_memo
        )

        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to build distribution sketch: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/distribution/sketch/calculate")
async def calculate_distribution_from_sketch(
    run_id: Optional[str] = Query(default=None, description="특정 run의 샘플 사용 (NULL이면 최신)"),
    supabase_service: SupabaseService = Depends(get_supabase_service),
):
    """
    샘플 기반 전역 분포 계산.

    similarity_samples 테이블의 샘플을 사용하여 p0-p100 백분위수를 계산하고
    similarity_distribution_cache에 저장합니다.

    Args:
        run_id: 특정 run의 샘플 사용 (NULL이면 최신)

    Returns:
        {
            "success": bool,
            "distribution": {
                "p0": float, "p10": float, ..., "p100": float,
                "mean": float, "stddev": float
            },
            "cached": bool,
            "is_approximate": bool,
            "sample_count": int,
            "duration_ms": int
        }

    Example:
        POST /pipeline/distribution/sketch/calculate
    """
    try:
        result = await supabase_service.calculate_distribution_from_sketch(
            p_run_id=run_id
        )

        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to calculate distribution from sketch: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
