"""
Pipeline 라우터.

RAW → NORMALIZED → ZK → Essay 전체 파이프라인 엔드포인트.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from schemas.raw import ImportResult, RawNoteCreate
from schemas.normalized import ThoughtUnitCreate
from schemas.zk import ThoughtPairCandidate, ThoughtPairCreate, PairScore
from services.notion_service import NotionService
from services.ai_service import AIService, get_ai_service
from services.supabase_service import SupabaseService, get_supabase_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.post("/import-from-notion", response_model=ImportResult)
async def import_from_notion(
    page_size: int = Query(default=100, ge=1, le=100, description="가져올 메모 수"),
    supabase_service: SupabaseService = Depends(get_supabase_service),
):
    """
    Step 1: Notion DB에서 메모를 가져와 RAW 테이블에 저장.

    Args:
        page_size: 가져올 메모 수 (1-100)
        supabase_service: Supabase 서비스 (DI)

    Returns:
        ImportResult: import 성공/실패 결과

    Process:
        1. NotionService.query_database() 호출
        2. 각 페이지의 제목, properties 추출
        3. RAW 테이블에 upsert (notion_page_id 기준 중복 방지)
    """
    result = ImportResult(success=False)

    try:
        # Notion 서비스 초기화
        notion_service = NotionService()

        # Notion DB 조회
        logger.info(f"Querying Notion database (page_size={page_size})...")
        query_result = await notion_service.query_database(page_size=page_size)

        if not query_result.get("success"):
            raise HTTPException(
                status_code=500,
                detail=f"Notion query failed: {query_result.get('error')}",
            )

        pages = query_result.get("pages", [])
        total_count = len(pages)
        logger.info(f"Retrieved {total_count} pages from Notion")

        # 각 페이지를 RAW 테이블에 저장
        imported_count = 0
        skipped_count = 0

        for page in pages:
            try:
                # Notion 페이지 데이터 추출
                page_id = page.get("id")
                properties = page.get("properties", {})

                # 제목 추출 (properties에서 "제목" 또는 "Name" 필드 찾기)
                title = None
                for key in ["제목", "Name", "이름", "title"]:
                    if key in properties and properties[key]:
                        title = properties[key]
                        break

                # URL 생성
                notion_url = page.get("url") or f"https://notion.so/{page_id.replace('-', '')}"

                # RawNoteCreate 객체 생성
                raw_note = RawNoteCreate(
                    notion_page_id=page_id,
                    notion_url=notion_url,
                    title=title if title and isinstance(title, str) else None,
                    content=None,  # Step 1에서는 properties만, 본문은 나중에 필요 시 가져옴
                    properties_json=properties,
                    notion_created_time=datetime.fromisoformat(
                        page.get("created_time").replace("Z", "+00:00")
                    ),
                    notion_last_edited_time=datetime.fromisoformat(
                        page.get("last_edited_time").replace("Z", "+00:00")
                    ),
                )

                # Supabase에 upsert
                await supabase_service.upsert_raw_note(raw_note)
                imported_count += 1

            except Exception as e:
                logger.warning(f"Failed to import page {page.get('id')}: {e}")
                skipped_count += 1
                result.errors.append(f"Page {page.get('id')}: {str(e)}")

        # 결과 반환
        result.success = True
        result.imported_count = imported_count
        result.skipped_count = skipped_count

        logger.info(
            f"Import completed: {imported_count} imported, {skipped_count} skipped"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Import failed: {e}", exc_info=True)
        result.errors.append(str(e))
        raise HTTPException(status_code=500, detail=str(e))

    return result


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
    min_score: int = Query(default=65, ge=0, le=100, description="최소 창의적 연결 점수 (threshold)"),
    top_n: int = Query(default=5, ge=1, le=20, description="선택할 페어 개수"),
    supabase_service: SupabaseService = Depends(get_supabase_service),
    ai_service: AIService = Depends(get_ai_service),
):
    """
    Step 3: 낮은 유사도 범위 내 후보 쌍을 찾고, Claude로 평가하여 상위 N개를 DB에 저장.

    Args:
        min_similarity: 최소 유사도 (0-1, 기본 0.05, 낮을수록 서로 다른 아이디어)
        max_similarity: 최대 유사도 (0-1, 기본 0.35)
        min_score: 최소 창의적 연결 점수 (0-100, 기본 65, threshold 필터링용)
        top_n: 선택할 페어 개수 (1-20)
        supabase_service: Supabase 서비스 (DI)
        ai_service: AI 서비스 (DI)

    Returns:
        dict: 처리 결과 (후보 수, threshold 필터 후 개수, 선택된 페어 수, 페어 목록)

    Process:
        1. min < max 검증
        2. find_candidate_pairs() 호출 (서로 다른 raw_note에서만)
        3. 후보 없으면 Fallback 전략 (범위 확대)
        4. ThoughtPairCandidate 객체 리스트 생성
        5. score_pairs() 호출 (Claude 평가)
        6. min_score 이상인 쌍만 필터링 (threshold)
        7. 점수 기준 정렬 및 상위 top_n개 선택
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

        # 2. 후보 쌍 찾기 (Fallback 전략 포함)
        logger.info(
            f"Finding candidate pairs (similarity: {min_similarity}-{max_similarity})..."
        )
        candidates = await supabase_service.find_candidate_pairs(
            min_similarity=min_similarity,
            max_similarity=max_similarity
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
                    max_similarity=fb_max
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

        # 7. 점수 기준 정렬 및 상위 top_n개 선택
        sorted_pairs = sorted(
            filtered_pairs,
            key=lambda x: x.logical_expansion_score,
            reverse=True
        )
        selected_pairs = sorted_pairs[:min(top_n, len(sorted_pairs))]

        logger.info(f"Selected top {len(selected_pairs)} pairs")

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
    top_n: int = Query(default=5, ge=1, le=20, description="Step 3용 페어 개수"),
    supabase_service: SupabaseService = Depends(get_supabase_service),
    ai_service: AIService = Depends(get_ai_service),
):
    """
    전체 파이프라인 (Step 1 → Step 2 → Step 3) 순차 실행.

    Args:
        page_size: Step 1에서 가져올 메모 수 (1-100)
        min_similarity: Step 3용 최소 유사도 (0-1, 기본 0.05 = 낮은 유사도)
        max_similarity: Step 3용 최대 유사도 (0-1, 기본 0.35)
        min_score: Step 3용 최소 창의적 연결 점수 (0-100, 기본 65)
        top_n: Step 3용 선택할 페어 개수 (1-20)
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
        "errors": [],
    }

    try:
        logger.info("Starting full pipeline (Step 1 → Step 2 → Step 3)...")

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
                top_n=top_n,
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

        # 성공 여부 판단 (적어도 Step 1이 성공했으면 부분 성공)
        result["success"] = result["step1"].get("imported", 0) > 0

        logger.info(
            f"Full pipeline completed: "
            f"Step1={result['step1']}, Step2={result['step2']}, Step3={result['step3']}"
        )

    except Exception as e:
        logger.error(f"Full pipeline failed: {e}", exc_info=True)
        result["errors"].append(str(e))
        raise HTTPException(status_code=500, detail=str(e))

    return result
