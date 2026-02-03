"""
Supabase 서비스.

PostgreSQL + pgvector를 사용한 데이터 CRUD.
"""

import logging
import time
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from uuid import UUID

from supabase import create_async_client, AsyncClient

from config import settings
from schemas.raw import RawNote, RawNoteCreate
from schemas.normalized import ThoughtUnitCreate
from schemas.zk import (
    ThoughtPairCreate,
    PairCandidateCreate,
    PairCandidateBatch,
    ThoughtPairCreateExtended,
)

logger = logging.getLogger(__name__)


class SupabaseService:
    """Supabase CRUD + 연결 풀링."""

    def __init__(self):
        """
        Supabase 클라이언트 초기화.

        HTTP 연결 풀링을 통한 성능 최적화.
        """
        # Supabase async 클라이언트
        self.client: AsyncClient = None
        self._initialized = False

    async def _ensure_initialized(self):
        """클라이언트 초기화 (최초 호출 시)"""
        if not self._initialized:
            self.client = await create_async_client(
                settings.supabase_url, settings.supabase_key
            )
            self._initialized = True

    async def close(self):
        """HTTP 클라이언트 종료."""
        # Supabase 내장 클라이언트 사용 시 별도 종료 불필요
        pass

    # ============================================================
    # RAW Notes CRUD
    # ============================================================

    async def upsert_raw_note(self, note: RawNoteCreate) -> dict:
        """
        RAW note 저장 (notion_page_id 기준 upsert).

        Args:
            note: 저장할 RAW note

        Returns:
            저장된 note 데이터

        Raises:
            Exception: DB 저장 실패 시
        """
        await self._ensure_initialized()

        try:
            # JSON 직렬화 가능한 형식으로 변환 (datetime → ISO string)
            data = note.model_dump(mode='json')

            # Supabase upsert (conflict on notion_page_id)
            response = await (
                self.client.table("raw_notes")
                .upsert(data, on_conflict="notion_page_id")
                .execute()
            )

            if response.data:
                logger.info(
                    f"Raw note upserted: {note.notion_page_id} (title: {note.title[:50] if note.title else 'N/A'}, content: {len(note.content) if note.content else 0} chars)"
                )
                return response.data[0]
            else:
                raise Exception("Upsert returned no data")

        except Exception as e:
            logger.error(f"Failed to upsert raw note {note.notion_page_id}: {e}")
            raise

    async def get_raw_note_ids(self) -> List[str]:
        """
        모든 활성 RAW note의 ID 목록 조회 (메모리 절약).

        Returns:
            UUID 목록 (삭제된 페이지 제외)
        """
        await self._ensure_initialized()

        try:
            response = await (
                self.client.table("raw_notes")
                .select("id")
                .eq("is_deleted", False)
                .execute()
            )

            ids = [row["id"] for row in response.data]
            logger.info(f"Retrieved {len(ids)} active raw note IDs")
            return ids

        except Exception as e:
            logger.error(f"Failed to get raw note IDs: {e}")
            raise

    async def get_raw_notes_by_ids(self, note_ids: List[str]) -> List[dict]:
        """
        ID 목록으로 활성 RAW notes 조회 (배치별 full content 로드).

        Args:
            note_ids: UUID 목록

        Returns:
            RAW note 데이터 목록 (삭제된 페이지 제외)
        """
        await self._ensure_initialized()

        try:
            response = await (
                self.client.table("raw_notes")
                .select("*")
                .in_("id", note_ids)
                .eq("is_deleted", False)
                .execute()
            )

            logger.info(f"Retrieved {len(response.data)} active raw notes by IDs")
            return response.data

        except Exception as e:
            logger.error(f"Failed to get raw notes by IDs: {e}")
            raise

    async def get_raw_note_count(self) -> int:
        """활성 RAW notes 총 개수 조회 (삭제된 페이지 제외)."""
        await self._ensure_initialized()

        try:
            response = await (
                self.client.table("raw_notes")
                .select("id", count="exact")
                .eq("is_deleted", False)
                .execute()
            )

            count = response.count if response.count else 0
            logger.info(f"Total active raw notes: {count}")
            return count

        except Exception as e:
            logger.error(f"Failed to get raw note count: {e}")
            return 0

    # ============================================================
    # Thought Units CRUD
    # ============================================================

    async def insert_thought_unit(self, thought: ThoughtUnitCreate) -> dict:
        """
        Thought unit 한 개 저장.

        Args:
            thought: 저장할 사고 단위 (임베딩 포함)

        Returns:
            저장된 thought unit 데이터

        Raises:
            Exception: DB 저장 실패 시
        """
        await self._ensure_initialized()

        try:
            # JSON 직렬화 가능한 형식으로 변환
            data = thought.model_dump(mode='json')

            # pgvector는 list[float]를 자동으로 vector 타입으로 변환
            response = await (
                self.client.table("thought_units")
                .insert(data)
                .execute()
            )

            if response.data:
                logger.info(
                    f"Thought unit inserted: ID={response.data[0]['id']}, "
                    f"raw_note_id={thought.raw_note_id}, "
                    f"claim={thought.claim[:50]}..."
                )
                return response.data[0]
            else:
                raise Exception("Insert returned no data")

        except Exception as e:
            logger.error(
                f"Failed to insert thought unit for raw_note {thought.raw_note_id}: {e}"
            )
            raise

    async def insert_thought_units_batch(
        self, thoughts: List[ThoughtUnitCreate]
    ) -> List[dict]:
        """
        여러 thought units 배치 저장.

        Args:
            thoughts: 저장할 사고 단위 목록

        Returns:
            저장된 thought units 데이터 목록

        Raises:
            Exception: DB 저장 실패 시
        """
        await self._ensure_initialized()

        if not thoughts:
            logger.warning("Batch insert called with empty list")
            return []

        try:
            # 모든 thought를 JSON 직렬화
            data = [thought.model_dump(mode='json') for thought in thoughts]

            # 배치 insert
            response = await (
                self.client.table("thought_units")
                .insert(data)
                .execute()
            )

            if response.data:
                logger.info(
                    f"Batch inserted {len(response.data)} thought units "
                    f"(raw_note_ids: {set(t.raw_note_id for t in thoughts)})"
                )
                return response.data
            else:
                raise Exception("Batch insert returned no data")

        except Exception as e:
            logger.error(
                f"Failed to batch insert {len(thoughts)} thought units: {e}"
            )
            raise

    async def get_thought_units_by_raw_note(self, raw_note_id: str) -> List[dict]:
        """
        특정 raw_note의 모든 thought units 조회.

        Args:
            raw_note_id: 원본 메모 UUID

        Returns:
            Thought units 목록
        """
        await self._ensure_initialized()

        try:
            response = await (
                self.client.table("thought_units")
                .select("*")
                .eq("raw_note_id", raw_note_id)
                .order("id")
                .execute()
            )

            logger.info(
                f"Retrieved {len(response.data)} thought units for raw_note {raw_note_id}"
            )
            return response.data

        except Exception as e:
            logger.error(
                f"Failed to get thought units for raw_note {raw_note_id}: {e}"
            )
            raise

    async def get_all_thought_units_with_embeddings(self) -> List[dict]:
        """
        임베딩이 있는 모든 thought units 조회 (유사도 검색용).

        Returns:
            Thought units 목록 (임베딩 포함)
        """
        await self._ensure_initialized()

        try:
            response = await (
                self.client.table("thought_units")
                .select("id, raw_note_id, claim, context, embedding, embedding_model, extracted_at")
                .not_.is_("embedding", "null")
                .order("id")
                .execute()
            )

            logger.info(
                f"Retrieved {len(response.data)} thought units with embeddings"
            )
            return response.data

        except Exception as e:
            logger.error(f"Failed to get thought units with embeddings: {e}")
            raise

    # ============================================================
    # Thought Pairs CRUD (Step 3: ZK 레이어)
    # ============================================================

    async def find_candidate_pairs(
        self,
        min_similarity: float = 0.05,
        max_similarity: float = 0.35,
        top_k: int = 30,
        limit: int = 20
    ) -> List[dict]:
        """
        Top-K 알고리즘으로 후보 페어 조회 (HNSW 인덱스 활용).

        Args:
            min_similarity: 최소 유사도 (기본 0.05, 낮은 유사도 = 서로 다른 아이디어)
            max_similarity: 최대 유사도 (기본 0.35, 약한 연결 = 창의적 확장 가능)
            top_k: 각 thought당 검색할 상위 K개 (기본 30)
            limit: 최종 반환할 최대 개수 (기본 20)

        Returns:
            후보 쌍 목록 (thought_a_id, thought_b_id, similarity_score, thought_a_claim, thought_b_claim)

        Performance:
            - 복잡도: O(n × K) (기존 O(n²)에서 98% 개선)
            - 실행 시간: ~5초 (기존 60초+ 타임아웃)
            - HNSW 인덱스 자동 활용

        Raises:
            Exception: Stored Procedure 호출 실패 시
        """
        await self._ensure_initialized()

        try:
            response = await self.client.rpc(
                "find_similar_pairs_topk",
                {
                    "min_sim": min_similarity,
                    "max_sim": max_similarity,
                    "top_k": top_k,
                    "lim": limit
                }
            ).execute()

            logger.info(
                f"Found {len(response.data)} candidate pairs with weak connections "
                f"(similarity: {min_similarity:.2f}-{max_similarity:.2f}, top_k={top_k})"
            )
            return response.data

        except Exception as e:
            error_msg = str(e)
            if "function" in error_msg.lower() and "does not exist" in error_msg.lower():
                logger.error(
                    "Stored procedure 'find_similar_pairs_topk' not found. "
                    "Please run docs/supabase_migrations/005_create_topk_function.sql"
                )
                raise Exception(
                    "Stored procedure 'find_similar_pairs_topk' not found. "
                    "Please run migration 005"
                )
            logger.error(f"Failed to find candidate pairs: {e}")
            raise

    async def insert_thought_pair(self, pair: ThoughtPairCreate) -> dict:
        """
        Thought pair 한 개 저장 (UPSERT).

        Args:
            pair: 저장할 페어 데이터

        Returns:
            저장된 thought pair 데이터

        Raises:
            Exception: DB 저장 실패 시
        """
        await self._ensure_initialized()

        try:
            # JSON 직렬화 가능한 형식으로 변환
            data = pair.model_dump(mode='json')

            # UPSERT (thought_a_id, thought_b_id 조합으로 중복 방지)
            # is_used_in_essay는 업데이트하지 않음
            response = await (
                self.client.table("thought_pairs")
                .upsert(data, on_conflict="thought_a_id,thought_b_id")
                .execute()
            )

            if response.data:
                logger.info(
                    f"Thought pair upserted: ID={response.data[0]['id']}, "
                    f"thoughts=({pair.thought_a_id}, {pair.thought_b_id}), "
                    f"similarity={pair.similarity_score:.3f}"
                )
                return response.data[0]
            else:
                raise Exception("Upsert returned no data")

        except Exception as e:
            logger.error(
                f"Failed to upsert thought pair ({pair.thought_a_id}, {pair.thought_b_id}): {e}"
            )
            raise

    async def insert_thought_pairs_batch(
        self, pairs: List[ThoughtPairCreate]
    ) -> List[dict]:
        """
        여러 thought pairs 배치 저장 (UPSERT).

        Args:
            pairs: 저장할 페어 목록

        Returns:
            저장된 thought pairs 데이터 목록

        Raises:
            Exception: DB 저장 실패 시
        """
        await self._ensure_initialized()

        if not pairs:
            logger.warning("Batch insert called with empty pairs list")
            return []

        try:
            # 모든 pair를 JSON 직렬화
            data = [pair.model_dump(mode='json') for pair in pairs]

            # 배치 UPSERT
            response = await (
                self.client.table("thought_pairs")
                .upsert(data, on_conflict="thought_a_id,thought_b_id")
                .execute()
            )

            if response.data:
                logger.info(
                    f"Batch upserted {len(response.data)} thought pairs "
                    f"(avg similarity: {sum(p.similarity_score for p in pairs) / len(pairs):.3f})"
                )
                return response.data
            else:
                raise Exception("Batch upsert returned no data")

        except Exception as e:
            logger.error(f"Failed to batch upsert {len(pairs)} thought pairs: {e}")
            raise

    async def get_unused_thought_pairs(self, limit: int = 10) -> List[dict]:
        """
        미사용 thought pairs 조회 (에세이 생성용).

        Args:
            limit: 반환할 최대 개수 (기본 10)

        Returns:
            미사용 thought pairs 목록 (similarity_score ASC 정렬 - 낮은 유사도부터)
        """
        await self._ensure_initialized()

        try:
            response = await (
                self.client.table("thought_pairs")
                .select("*")
                .eq("is_used_in_essay", False)
                .order("similarity_score", desc=False)  # 낮은 유사도부터 선택 (창의적 조합)
                .limit(limit)
                .execute()
            )

            logger.info(
                f"Retrieved {len(response.data)} unused thought pairs (limit: {limit})"
            )
            return response.data

        except Exception as e:
            logger.error(f"Failed to get unused thought pairs: {e}")
            raise

    async def update_pair_used_status(
        self, pair_id: int, is_used: bool = True
    ) -> dict:
        """
        Thought pair 사용 상태 업데이트.

        Args:
            pair_id: 페어 ID
            is_used: 사용 여부 (기본 True)

        Returns:
            업데이트된 thought pair 데이터

        Raises:
            Exception: DB 업데이트 실패 시
        """
        await self._ensure_initialized()

        try:
            response = await (
                self.client.table("thought_pairs")
                .update({"is_used_in_essay": is_used})
                .eq("id", pair_id)
                .execute()
            )

            if response.data:
                logger.info(
                    f"Updated thought pair {pair_id} used status: {is_used}"
                )
                return response.data[0]
            else:
                raise Exception(f"Thought pair {pair_id} not found")

        except Exception as e:
            logger.error(f"Failed to update pair {pair_id} used status: {e}")
            raise

    async def get_pair_with_thoughts(self, pair_id: int) -> dict:
        """
        Thought pair + 사고 단위 + 원본 메모 정보 JOIN 조회.

        Args:
            pair_id: 페어 ID

        Returns:
            페어 정보 + 양쪽 thought의 claim/context + 원본 메모 title/url

        Raises:
            Exception: DB 조회 실패 시
        """
        await self._ensure_initialized()

        try:
            # Step 1: 페어 기본 정보 조회
            pair_response = await (
                self.client.table("thought_pairs")
                .select("*")
                .eq("id", pair_id)
                .single()
                .execute()
            )

            if not pair_response.data:
                raise Exception(f"Thought pair {pair_id} not found")

            pair_data = pair_response.data

            # Step 2: 두 개의 thought units 조회
            thought_a_response = await (
                self.client.table("thought_units")
                .select("id, claim, context, raw_note_id")
                .eq("id", pair_data["thought_a_id"])
                .single()
                .execute()
            )

            thought_b_response = await (
                self.client.table("thought_units")
                .select("id, claim, context, raw_note_id")
                .eq("id", pair_data["thought_b_id"])
                .single()
                .execute()
            )

            thought_a = thought_a_response.data
            thought_b = thought_b_response.data

            # Step 3: 두 개의 raw notes 조회
            raw_note_a_response = await (
                self.client.table("raw_notes")
                .select("id, title, notion_url")
                .eq("id", thought_a["raw_note_id"])
                .single()
                .execute()
            )

            raw_note_b_response = await (
                self.client.table("raw_notes")
                .select("id, title, notion_url")
                .eq("id", thought_b["raw_note_id"])
                .single()
                .execute()
            )

            raw_note_a = raw_note_a_response.data
            raw_note_b = raw_note_b_response.data

            # Step 4: 결과 조합
            result = {
                "pair_id": pair_data["id"],
                "similarity_score": pair_data["similarity_score"],
                "connection_reason": pair_data["connection_reason"],
                "is_used_in_essay": pair_data["is_used_in_essay"],
                "selected_at": pair_data["selected_at"],
                "thought_a": {
                    "id": thought_a["id"],
                    "claim": thought_a["claim"],
                    "context": thought_a["context"],
                    "source_title": raw_note_a["title"],
                    "source_url": raw_note_a["notion_url"]
                },
                "thought_b": {
                    "id": thought_b["id"],
                    "claim": thought_b["claim"],
                    "context": thought_b["context"],
                    "source_title": raw_note_b["title"],
                    "source_url": raw_note_b["notion_url"]
                }
            }

            logger.info(
                f"Retrieved pair {pair_id} with full thought details "
                f"(thoughts: {thought_a['id']}, {thought_b['id']})"
            )
            return result

        except Exception as e:
            logger.error(f"Failed to get pair {pair_id} with thoughts: {e}")
            raise

    # ========================
    # Essay CRUD Methods (Step 4)
    # ========================

    async def insert_essay(self, essay: "EssayCreate") -> dict:
        """
        essays 테이블에 단일 에세이 저장.

        Args:
            essay: EssayCreate 모델 인스턴스

        Returns:
            {
                "id": int,
                "type": str,
                "title": str,
                "outline": list[str],
                "used_thoughts_json": list[dict],
                "reason": str,
                "pair_id": int,
                "generated_at": str (ISO format)
            }

        Raises:
            Exception: DB 저장 실패 시
        """
        await self._ensure_initialized()

        try:
            # JSONB 필드 직렬화
            essay_dict = {
                "type": essay.type,
                "title": essay.title,
                "outline": essay.outline,  # list → JSONB (자동)
                "used_thoughts_json": [t.model_dump() for t in essay.used_thoughts],  # JSONB
                "reason": essay.reason,
                "pair_id": essay.pair_id
            }

            response = await self.client.table("essays")\
                .insert(essay_dict)\
                .execute()

            inserted = response.data[0]
            logger.info(f"Inserted essay ID {inserted['id']} for pair {essay.pair_id}")
            return inserted

        except Exception as e:
            logger.error(f"Failed to insert essay: {e}")
            logger.error(f"Essay data: {essay_dict}")
            raise

    async def insert_essays_batch(self, essays: List["EssayCreate"]) -> List[dict]:
        """
        여러 에세이 배치 저장.

        Args:
            essays: EssayCreate 모델 리스트

        Returns:
            저장된 에세이 리스트

        Note:
            - UPSERT는 하지 않음 (중복 방지는 pair_id 외래키로 보장)
            - 실패 시 전체 롤백
        """
        await self._ensure_initialized()

        if not essays:
            return []

        try:
            essays_dict = [
                {
                    "type": e.type,
                    "title": e.title,
                    "outline": e.outline,
                    "used_thoughts_json": [t.model_dump() for t in e.used_thoughts],
                    "reason": e.reason,
                    "pair_id": e.pair_id
                }
                for e in essays
            ]

            response = await self.client.table("essays")\
                .insert(essays_dict)\
                .execute()

            inserted = response.data
            logger.info(f"Batch inserted {len(inserted)} essays")
            return inserted

        except Exception as e:
            logger.error(f"Failed to batch insert essays: {e}")
            raise

    async def get_essays(
        self,
        limit: int = 10,
        offset: int = 0
    ) -> List[dict]:
        """
        essays 테이블 조회 (최신순).

        Args:
            limit: 최대 반환 개수 (기본 10)
            offset: 건너뛸 개수 (페이지네이션)

        Returns:
            에세이 리스트 (JSONB 필드 자동 파싱됨)
        """
        await self._ensure_initialized()

        try:
            response = await self.client.table("essays")\
                .select("*")\
                .order("generated_at", desc=True)\
                .limit(limit)\
                .offset(offset)\
                .execute()

            essays = response.data
            logger.info(f"Retrieved {len(essays)} essays")
            return essays

        except Exception as e:
            logger.error(f"Failed to get essays: {e}")
            raise

    async def get_essay_by_id(self, essay_id: int) -> dict:
        """
        단일 에세이 조회.

        Args:
            essay_id: 에세이 ID

        Returns:
            에세이 데이터

        Raises:
            Exception: 에세이가 없거나 조회 실패 시
        """
        await self._ensure_initialized()

        try:
            response = await self.client.table("essays")\
                .select("*")\
                .eq("id", essay_id)\
                .single()\
                .execute()

            essay = response.data
            logger.info(f"Retrieved essay ID {essay_id}")
            return essay

        except Exception as e:
            logger.error(f"Failed to get essay {essay_id}: {e}")
            raise

    # ============================================================
    # Import Jobs CRUD (Background Task Tracking)
    # ============================================================

    async def create_import_job(self, job: "ImportJobCreate") -> dict:
        """
        Create new import job record.

        Args:
            job: Import job creation data

        Returns:
            dict: Created job record with UUID

        Raises:
            Exception: Job creation failed
        """
        await self._ensure_initialized()

        try:
            data = {
                "status": "pending",
                "mode": job.mode,
                "config_json": job.config_json
            }
            response = await self.client.table("import_jobs").insert(data).execute()
            created = response.data[0]
            logger.info(f"Created import job {created['id']} (mode: {job.mode})")
            return created
        except Exception as e:
            logger.error(f"Failed to create import job: {e}")
            raise

    async def update_import_job(self, job_id: str, updates: "ImportJobUpdate") -> dict:
        """
        Update import job progress.

        Args:
            job_id: UUID of import job
            updates: Fields to update

        Returns:
            dict: Updated job record

        Raises:
            Exception: Job not found or update failed
        """
        await self._ensure_initialized()

        try:
            data = updates.model_dump(exclude_none=True, mode='json')
            response = await self.client.table("import_jobs")\
                .update(data).eq("id", job_id).execute()

            if not response.data:
                raise Exception(f"Import job {job_id} not found")

            return response.data[0]
        except Exception as e:
            logger.error(f"Failed to update import job {job_id}: {e}")
            raise

    async def get_import_job(self, job_id: str) -> dict:
        """
        Retrieve import job by ID.

        Args:
            job_id: UUID of import job

        Returns:
            dict: Job record

        Raises:
            Exception: Job not found
        """
        await self._ensure_initialized()

        try:
            response = await self.client.table("import_jobs")\
                .select("*").eq("id", job_id).single().execute()

            if not response.data:
                raise Exception(f"Import job {job_id} not found")

            return response.data
        except Exception as e:
            logger.error(f"Failed to get import job {job_id}: {e}")
            raise

    async def get_pages_to_fetch(
        self,
        notion_pages: List[Dict[str, Any]]
    ) -> tuple[List[str], List[str], List[str]]:
        """
        Compare Notion pages with DB using server-side RPC.

        Uses PostgreSQL function for efficient change detection.
        Falls back to full table scan if RPC fails.

        Args:
            notion_pages: List of page metadata from Notion API
                Each page must have: id, last_edited_time

        Returns:
            Tuple of (new_page_ids, updated_page_ids, deleted_page_ids)

        Performance:
            - RPC mode: ~150ms (constant time, scales to 100k pages)
            - Fallback mode: ~110ms (current size)
            - Network: Only changed pages (0.5KB vs 60KB)

        Example:
            >>> pages = [{"id": "abc", "last_edited_time": "2024-01-15T14:30:00.000Z"}]
            >>> new, updated, deleted = await service.get_pages_to_fetch(pages)
            >>> print(f"New: {len(new)}, Updated: {len(updated)}, Deleted: {len(deleted)}")
        """
        await self._ensure_initialized()

        # Prepare data for RPC
        pages_json = []
        force_new_ids = []  # Pages with invalid timestamps → treat as new

        for p in notion_pages:
            page_id = p.get("id")
            last_edited = p.get("last_edited_time")

            if not page_id:
                logger.warning("Page missing 'id' field, skipping")
                continue

            if not last_edited:
                logger.warning(f"Page {page_id} missing 'last_edited_time', treating as new")
                force_new_ids.append(page_id)
                continue

            try:
                # Parse ISO 8601 timestamp
                notion_time = datetime.fromisoformat(last_edited.replace("Z", "+00:00"))

                # Truncate to seconds (match SQL function behavior)
                notion_time = notion_time.replace(microsecond=0)

                pages_json.append({
                    "id": page_id,
                    "last_edited": notion_time.isoformat()
                })
            except (ValueError, AttributeError, TypeError) as e:
                logger.warning(f"Invalid timestamp for {page_id}: {e}, treating as new")
                force_new_ids.append(page_id)

        if not pages_json and not force_new_ids:
            logger.warning("No valid pages to check")
            return [], [], []

        logger.info(f"Change detection: checking {len(pages_json)} pages via RPC (sample: {[p['id'] for p in pages_json[:3]]})")

        # Try RPC change detection (Solution 3)
        try:
            start_time = time.time()

            response = await self.client.rpc('get_changed_pages', {
                'pages_data': pages_json
            }).execute()

            elapsed = time.time() - start_time

            # Validate response structure
            if not response.data or not isinstance(response.data, dict):
                raise ValueError("Invalid RPC response format: expected dict")

            result = response.data

            # Check for SQL function error
            if 'error' in result:
                raise ValueError(f"SQL function error: {result['error']} (SQLSTATE: {result.get('error_detail', 'unknown')})")

            # Extract results
            new_page_ids = result.get('new_page_ids', [])
            updated_page_ids = result.get('updated_page_ids', [])
            deleted_page_ids = result.get('deleted_page_ids', [])

            # Validate types
            if not isinstance(new_page_ids, list):
                raise ValueError(f"Invalid type for new_page_ids: {type(new_page_ids)}")
            if not isinstance(updated_page_ids, list):
                raise ValueError(f"Invalid type for updated_page_ids: {type(updated_page_ids)}")
            if not isinstance(deleted_page_ids, list):
                raise ValueError(f"Invalid type for deleted_page_ids: {type(deleted_page_ids)}")

            # Add force_new pages
            new_page_ids.extend(force_new_ids)

            # Validate UUIDs
            import re
            UUID_PATTERN = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)

            for page_id in new_page_ids + updated_page_ids + deleted_page_ids:
                if not UUID_PATTERN.match(page_id):
                    raise ValueError(f"Invalid UUID format: {page_id}")

            logger.info(
                f"✅ RPC change detection completed in {elapsed:.2f}s: "
                f"{len(new_page_ids)} new, {len(updated_page_ids)} updated, "
                f"{len(deleted_page_ids)} deleted, "
                f"{result.get('unchanged_count', len(pages_json) - len(new_page_ids) - len(updated_page_ids))} unchanged"
            )

            return new_page_ids, updated_page_ids, deleted_page_ids

        except Exception as rpc_error:
            logger.error(f"❌ RPC change detection failed: {rpc_error}, falling back to full table scan")

            # Fallback: Full table scan (방식 A)
            try:
                logger.info("Using fallback: full table scan")

                response = await (
                    self.client.table("raw_notes")
                    .select("notion_page_id, notion_last_edited_time")
                    .execute()
                )

                # Build existing_map from DB
                existing_map = {}
                for row in response.data:
                    db_page_id = row["notion_page_id"]
                    db_time = row["notion_last_edited_time"]

                    # Parse timestamp
                    if isinstance(db_time, str):
                        db_time = datetime.fromisoformat(db_time.replace("Z", "+00:00"))

                    # Ensure timezone-aware
                    if db_time.tzinfo is None:
                        db_time = db_time.replace(tzinfo=timezone.utc)

                    # Truncate to seconds
                    db_time = db_time.replace(microsecond=0)
                    existing_map[db_page_id] = db_time

                # Build page_map from Notion pages
                page_map = {}
                for p_json in pages_json:
                    page_id = p_json["id"]
                    notion_time = datetime.fromisoformat(p_json["last_edited"])
                    page_map[page_id] = notion_time

                # Compare
                new_ids = []
                updated_ids = []

                for page_id, notion_time in page_map.items():
                    if page_id not in existing_map:
                        new_ids.append(page_id)
                    elif notion_time > existing_map[page_id]:
                        updated_ids.append(page_id)

                # Add force_new pages
                new_ids.extend(force_new_ids)

                # Detect deleted pages (in DB but not in Notion)
                all_notion_ids = set(page_map.keys())
                deleted_ids = [
                    db_id for db_id in existing_map.keys()
                    if db_id not in all_notion_ids
                ]

                logger.info(
                    f"✅ Fallback completed: {len(new_ids)} new, {len(updated_ids)} updated, "
                    f"{len(deleted_ids)} deleted, "
                    f"{len(page_map) - len(new_ids) - len(updated_ids)} unchanged"
                )

                return new_ids, updated_ids, deleted_ids

            except Exception as fallback_error:
                logger.error(f"❌ Fallback also failed: {fallback_error}, treating all as new (last resort)")

                # Last resort: treat all as new
                all_ids = [p["id"] for p in pages_json] + force_new_ids
                return all_ids, [], []

    async def validate_rpc_function_exists(self) -> bool:
        """
        Check if RPC function is deployed in Supabase.

        Returns:
            bool: True if function exists and works, False otherwise
        """
        try:
            await self._ensure_initialized()

            # Test with empty array
            response = await self.client.rpc('get_changed_pages', {
                'pages_data': []
            }).execute()

            # Validate response
            if not response.data or not isinstance(response.data, dict):
                logger.warning("⚠️  RPC function returned unexpected format")
                return False

            logger.info("✅ RPC function 'get_changed_pages' is available and working")
            return True

        except Exception as e:
            logger.warning(f"⚠️  RPC function 'get_changed_pages' not available: {e}")
            logger.warning("   Import will use fallback mode (full table scan)")
            return False

    async def soft_delete_raw_note(self, notion_page_id: str) -> None:
        """
        Mark a page as deleted without removing from DB (soft delete).

        This preserves the page and all downstream data (thought_units, essays)
        while marking it as deleted in Notion.

        Args:
            notion_page_id: Notion page ID to soft delete

        Raises:
            No exceptions - failures are logged only
        """
        await self._ensure_initialized()

        try:
            await self.client.table("raw_notes").update({
                "is_deleted": True,
                "deleted_at": datetime.now(timezone.utc).isoformat()
            }).eq("notion_page_id", notion_page_id).execute()

            logger.warning(f"🗑️  Soft deleted page: {notion_page_id} (essays preserved)")

        except Exception as e:
            logger.error(f"Failed to soft delete page {notion_page_id}: {e}")

    async def increment_job_progress(
        self,
        job_id: str,
        imported: bool = False,
        skipped: bool = False,
        failed_page: Optional[Dict[str, str]] = None
    ) -> None:
        """
        Atomically increment job progress counters.

        This method NEVER raises exceptions - failures are logged only.
        This ensures import continues even if progress tracking fails.

        Args:
            job_id: UUID of import job
            imported: True if page was successfully imported
            skipped: True if page was skipped
            failed_page: Dict with page_id and error_message if page failed
        """
        await self._ensure_initialized()

        try:
            job = await self.get_import_job(job_id)
            updates = {"processed_pages": job["processed_pages"] + 1}

            if imported:
                updates["imported_pages"] = job["imported_pages"] + 1
            if skipped:
                updates["skipped_pages"] = job["skipped_pages"] + 1
            if failed_page:
                current_failed = job.get("failed_pages", [])
                current_failed.append(failed_page)
                updates["failed_pages"] = current_failed

            await self.client.table("import_jobs").update(updates).eq("id", job_id).execute()
        except Exception as e:
            # ✅ CRITICAL: Don't raise - just log
            # Import continues even if progress tracking fails
            logger.error(f"Failed to increment job {job_id} progress: {e}")

    # ============================================================
    # Pair Candidates CRUD (하이브리드 C 전략)
    # ============================================================

    async def insert_pair_candidates_batch(
        self,
        candidates: List[PairCandidateCreate],
        batch_size: int = 1000
    ) -> PairCandidateBatch:
        """
        30,000개 후보를 pair_candidates 테이블에 대량 저장 (배치 처리).

        Args:
            candidates: 저장할 후보 쌍 목록 (예: 30,000개)
            batch_size: 배치당 처리할 개수 (기본 1000개)

        Returns:
            PairCandidateBatch: {
                inserted_count: int,    # 성공적으로 저장된 개수
                duplicate_count: int,   # 중복으로 스킵된 개수
                error_count: int        # 실패한 개수
            }

        Performance:
            - 30,000개 저장 < 3분
            - ON CONFLICT DO NOTHING (중복 자동 무시)

        Raises:
            Exception: DB 저장 실패 시 (전체 배치 롤백 아님)
        """
        await self._ensure_initialized()

        if not candidates:
            logger.warning("insert_pair_candidates_batch called with empty list")
            return PairCandidateBatch(
                inserted_count=0,
                duplicate_count=0,
                error_count=0
            )

        total_candidates = len(candidates)
        inserted_count = 0
        duplicate_count = 0
        error_count = 0

        logger.info(
            f"Starting batch insert: {total_candidates} candidates "
            f"(batch_size={batch_size})"
        )

        # 배치별 처리
        for i in range(0, total_candidates, batch_size):
            batch = candidates[i:i+batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (total_candidates + batch_size - 1) // batch_size

            try:
                # JSON 직렬화
                data = [candidate.model_dump(mode='json') for candidate in batch]

                # Supabase upsert (중복 시 무시)
                # ON CONFLICT (thought_a_id, thought_b_id) DO NOTHING
                response = await (
                    self.client.table("pair_candidates")
                    .upsert(data, on_conflict="thought_a_id,thought_b_id", ignore_duplicates=True)
                    .execute()
                )

                # Supabase는 중복 무시 시 data가 빈 배열로 반환됨
                current_inserted = len(response.data) if response.data else 0
                current_duplicate = len(batch) - current_inserted

                inserted_count += current_inserted
                duplicate_count += current_duplicate

                logger.info(
                    f"Batch {batch_num}/{total_batches}: "
                    f"{current_inserted} inserted, {current_duplicate} duplicates"
                )

            except Exception as e:
                error_count += len(batch)
                logger.error(
                    f"Failed to insert batch {batch_num}/{total_batches} "
                    f"({len(batch)} candidates): {e}"
                )

        result = PairCandidateBatch(
            inserted_count=inserted_count,
            duplicate_count=duplicate_count,
            error_count=error_count
        )

        logger.info(
            f"Batch insert completed: {inserted_count} inserted, "
            f"{duplicate_count} duplicates, {error_count} errors "
            f"(total: {total_candidates})"
        )

        return result

    async def get_pending_candidates(
        self,
        limit: int = 100,
        similarity_range: tuple[float, float] = (0.05, 0.35)
    ) -> List[dict]:
        """
        배치 워커가 미평가 후보 조회 (Claude 평가용).

        Args:
            limit: 반환할 최대 개수 (기본 100)
            similarity_range: (min_similarity, max_similarity) 범위 (기본 0.05-0.35)

        Returns:
            List[dict]: 미평가 후보 목록 (thought claim 포함 JOIN)
                각 dict 구조:
                {
                    "id": int,
                    "thought_a_id": int,
                    "thought_b_id": int,
                    "thought_a_claim": str,
                    "thought_b_claim": str,
                    "similarity": float,
                    "raw_note_id_a": str,
                    "raw_note_id_b": str
                }

        Performance:
            - <100ms (인덱스 활용)
            - FIFO 방식 (created_at ASC)

        Note:
            - llm_status='pending' AND llm_attempts < 3
            - thought_units와 2번 JOIN (claim 필요)
        """
        await self._ensure_initialized()

        min_sim, max_sim = similarity_range

        try:
            # pair_candidates에서 pending인 것만 조회 (필터링 먼저)
            response = await (
                self.client.table("pair_candidates")
                .select("*")
                .eq("llm_status", "pending")
                .lt("llm_attempts", 3)
                .gte("similarity", min_sim)
                .lte("similarity", max_sim)
                .order("created_at", desc=False)  # FIFO
                .limit(limit)
                .execute()
            )

            candidates = response.data

            if not candidates:
                logger.info("No pending candidates found")
                return []

            # thought_units 조회를 위한 ID 수집
            thought_ids = set()
            for c in candidates:
                thought_ids.add(c["thought_a_id"])
                thought_ids.add(c["thought_b_id"])

            # thought_units 한 번에 조회 (N+1 쿼리 방지)
            thoughts_response = await (
                self.client.table("thought_units")
                .select("id, claim")
                .in_("id", list(thought_ids))
                .execute()
            )

            # thought_id → claim 매핑
            thought_map = {t["id"]: t["claim"] for t in thoughts_response.data}

            # claim 추가
            result = []
            for c in candidates:
                thought_a_claim = thought_map.get(c["thought_a_id"])
                thought_b_claim = thought_map.get(c["thought_b_id"])

                # claim이 없으면 스킵 (데이터 정합성 문제)
                if not thought_a_claim or not thought_b_claim:
                    logger.warning(
                        f"Missing claim for candidate {c['id']}: "
                        f"thought_a={c['thought_a_id']}, thought_b={c['thought_b_id']}"
                    )
                    continue

                result.append({
                    "id": c["id"],
                    "thought_a_id": c["thought_a_id"],
                    "thought_b_id": c["thought_b_id"],
                    "thought_a_claim": thought_a_claim,
                    "thought_b_claim": thought_b_claim,
                    "similarity": c["similarity"],
                    "raw_note_id_a": c["raw_note_id_a"],
                    "raw_note_id_b": c["raw_note_id_b"]
                })

            logger.info(
                f"Retrieved {len(result)} pending candidates "
                f"(similarity: {min_sim:.2f}-{max_sim:.2f}, limit: {limit})"
            )
            return result

        except Exception as e:
            logger.error(f"Failed to get pending candidates: {e}")
            raise

    async def update_candidate_score(
        self,
        candidate_id: int,
        llm_score: int,
        connection_reason: str
    ) -> dict:
        """
        Claude 평가 결과를 pair_candidates에 업데이트.

        Args:
            candidate_id: 후보 ID
            llm_score: Claude 평가 점수 (0-100)
            connection_reason: Claude가 생성한 연결 이유

        Returns:
            dict: 업데이트된 candidate row

        Raises:
            Exception: DB 업데이트 실패 시
        """
        await self._ensure_initialized()

        try:
            # llm_attempts 증가를 위해 먼저 조회
            get_response = await (
                self.client.table("pair_candidates")
                .select("llm_attempts")
                .eq("id", candidate_id)
                .single()
                .execute()
            )

            if not get_response.data:
                raise Exception(f"Candidate {candidate_id} not found")

            current_attempts = get_response.data["llm_attempts"]

            # 업데이트
            update_data = {
                "llm_score": llm_score,
                "llm_status": "completed",
                "llm_attempts": current_attempts + 1,
                "last_evaluated_at": datetime.now(timezone.utc).isoformat(),
                "connection_reason": connection_reason,
                "evaluation_error": None  # 성공 시 에러 초기화
            }

            response = await (
                self.client.table("pair_candidates")
                .update(update_data)
                .eq("id", candidate_id)
                .execute()
            )

            if not response.data:
                raise Exception(f"Update returned no data for candidate {candidate_id}")

            updated = response.data[0]

            logger.info(
                f"Updated candidate {candidate_id}: score={llm_score}, "
                f"attempts={updated['llm_attempts']}"
            )

            return updated

        except Exception as e:
            logger.error(f"Failed to update candidate {candidate_id} score: {e}")
            raise

    async def move_to_thought_pairs(
        self,
        candidate_ids: List[int],
        min_score: int = 65
    ) -> int:
        """
        고득점 후보를 pair_candidates에서 thought_pairs로 이동.

        Args:
            candidate_ids: 이동할 후보 ID 목록
            min_score: 최소 점수 (기본 65, standard tier)

        Returns:
            int: 실제 이동된 페어 개수

        Logic:
            1. pair_candidates에서 조회 (score >= min_score)
            2. quality_tier 계산 (standard/premium/excellent)
            3. ThoughtPairCreateExtended 생성
            4. insert_thought_pairs_batch() 호출 (UPSERT)

        Quality Tiers:
            - standard: 65-84
            - premium: 85-94
            - excellent: 95-100

        Raises:
            Exception: DB 조회 또는 저장 실패 시
        """
        await self._ensure_initialized()

        if not candidate_ids:
            logger.warning("move_to_thought_pairs called with empty candidate_ids")
            return 0

        try:
            # Step 1: pair_candidates에서 조회 (score 필터링)
            response = await (
                self.client.table("pair_candidates")
                .select("*")
                .in_("id", candidate_ids)
                .gte("llm_score", min_score)
                .eq("llm_status", "completed")
                .execute()
            )

            candidates = response.data

            if not candidates:
                logger.info(
                    f"No candidates with score >= {min_score} found "
                    f"(checked {len(candidate_ids)} IDs)"
                )
                return 0

            # Step 2: quality_tier 계산 및 ThoughtPairCreateExtended 생성
            pairs_to_insert = []

            for c in candidates:
                llm_score = c["llm_score"]

                # quality_tier 계산
                if llm_score >= 95:
                    quality_tier = "excellent"
                elif llm_score >= 85:
                    quality_tier = "premium"
                else:
                    quality_tier = "standard"

                pair = ThoughtPairCreateExtended(
                    thought_a_id=c["thought_a_id"],
                    thought_b_id=c["thought_b_id"],
                    similarity_score=c["similarity"],
                    connection_reason=c.get("connection_reason", ""),
                    claude_score=llm_score,
                    quality_tier=quality_tier,
                    essay_content=None  # UI 프리뷰는 별도 생성
                )

                pairs_to_insert.append(pair)

            # Step 3: thought_pairs에 배치 저장 (UPSERT)
            # Note: insert_thought_pairs_batch()는 기존 메서드 재사용 불가
            # (claude_score, quality_tier 필드 없음)
            # 직접 upsert 수행

            if not pairs_to_insert:
                logger.warning("No pairs to insert after quality_tier calculation")
                return 0

            # JSON 직렬화
            data = [pair.model_dump(mode='json') for pair in pairs_to_insert]

            # 배치 UPSERT (중복 시 업데이트)
            upsert_response = await (
                self.client.table("thought_pairs")
                .upsert(data, on_conflict="thought_a_id,thought_b_id")
                .execute()
            )

            if not upsert_response.data:
                raise Exception("Batch upsert returned no data")

            moved_count = len(upsert_response.data)

            logger.info(
                f"Moved {moved_count} pairs to thought_pairs "
                f"(min_score={min_score}, quality tiers: "
                f"excellent={sum(1 for p in pairs_to_insert if p.quality_tier == 'excellent')}, "
                f"premium={sum(1 for p in pairs_to_insert if p.quality_tier == 'premium')}, "
                f"standard={sum(1 for p in pairs_to_insert if p.quality_tier == 'standard')})"
            )

            return moved_count

        except Exception as e:
            logger.error(
                f"Failed to move candidates to thought_pairs "
                f"({len(candidate_ids)} candidates): {e}"
            )
            raise

    # ============================================================
    # Distribution Cache (상대적 임계값 전략)
    # ============================================================

    async def get_similarity_distribution_cache(self) -> Optional[Dict[str, Any]]:
        """
        유사도 분포 캐시 조회.

        Returns:
            {
                "thought_count": 1921,
                "total_pairs": 38420,
                "percentiles": {"p0": 0.26, "p10": 0.30, ...},
                "mean": 0.38,
                "stddev": 0.05,
                "calculated_at": "2026-01-26T10:00:00",
                "duration_ms": 5432
            }
        """
        await self._ensure_initialized()

        try:
            response = await (
                self.client.table("similarity_distribution_cache")
                .select("*")
                .eq("id", 1)
                .maybe_single()
                .execute()
            )

            if not response.data:
                return None

            data = response.data

            # 백분위수를 딕셔너리로 변환
            percentiles = {
                "p0": data["p0"],
                "p10": data["p10"],
                "p20": data["p20"],
                "p30": data["p30"],
                "p40": data["p40"],
                "p50": data["p50"],
                "p60": data["p60"],
                "p70": data["p70"],
                "p80": data["p80"],
                "p90": data["p90"],
                "p100": data["p100"],
            }

            return {
                "thought_count": data["thought_unit_count"],
                "total_pairs": data["total_pair_count"],
                "percentiles": percentiles,
                "mean": data["mean"],
                "stddev": data["stddev"],
                "calculated_at": data["calculated_at"],
                "duration_ms": data.get("calculation_duration_ms"),
            }

        except Exception as e:
            logger.error(f"Failed to get distribution cache: {e}")
            raise

    async def calculate_similarity_distribution(self) -> Dict[str, Any]:
        """
        유사도 분포 계산 RPC 호출.

        NOTE: 이 메서드는 DEPRECATED. Distance Table에서 직접 계산하는
        calculate_distribution_from_distance_table()을 사용하세요.

        Returns:
            {
                "success": true,
                "thought_count": 1921,
                "total_pairs": 38420,
                "percentiles": {"p0": 0.26, ...},
                "mean": 0.38,
                "stddev": 0.05,
                "duration_ms": 5432
            }
        """
        await self._ensure_initialized()

        try:
            response = await self.client.rpc(
                "calculate_similarity_distribution"
            ).execute()

            if not response.data:
                raise Exception("RPC returned no data")

            result = response.data

            logger.info(
                f"Distribution calculated: {result.get('thought_count')} thoughts, "
                f"{result.get('total_pairs')} pairs, "
                f"{result.get('duration_ms')}ms"
            )

            return result

        except Exception as e:
            logger.error(f"Failed to calculate distribution: {e}")
            raise

    async def calculate_distribution_from_distance_table(self) -> Dict[str, Any]:
        """
        Distance Table 기반 유사도 분포 계산 (빠름).

        기존 calculate_similarity_distribution: thought_units CROSS JOIN → 60초+ 타임아웃
        신규: thought_pair_distances 집계 → 1초 미만

        Returns:
            {
                "success": true,
                "total_pairs": 1821186,
                "percentiles": {
                    "total_pairs": 1821186,
                    "p0": 0.001, "p10": 0.057, ..., "p100": 0.987,
                    "mean": 0.342, "stddev": 0.15
                },
                "duration_ms": 850,
                "cached": true
            }
        """
        await self._ensure_initialized()

        try:
            response = await self.client.rpc(
                "calculate_distribution_from_distance_table"
            ).execute()

            if not response.data:
                raise Exception("RPC returned no data")

            result = response.data

            if not result.get("success"):
                raise Exception(result.get("error", "Unknown error"))

            logger.info(
                f"Distribution calculated from Distance Table: "
                f"{result.get('total_pairs'):,} pairs, "
                f"{result.get('duration_ms')}ms"
            )

            return result

        except Exception as e:
            logger.error(f"Failed to calculate distribution from distance table: {e}")
            raise

    async def count_thought_units(self) -> int:
        """
        임베딩이 있는 thought_units 개수 조회.

        Returns:
            thought_units 개수
        """
        await self._ensure_initialized()

        try:
            response = await (
                self.client.table("thought_units")
                .select("id", count="exact")
                .not_.is_("embedding", "null")
                .execute()
            )

            count = response.count if response.count is not None else 0

            logger.debug(f"Thought units count: {count}")

            return count

        except Exception as e:
            logger.error(f"Failed to count thought units: {e}")
            raise

    # ============================================================
    # 샘플링 기반 마이닝 RPC (신규)
    # ============================================================

    async def mine_candidate_pairs(
        self,
        p_last_src_id: int = 0,
        p_src_batch: int = 30,
        p_dst_sample: int = 1200,
        p_k: int = 15,
        p_lo: float = 0.10,
        p_hi: float = 0.35,
        p_seed: int = 42,
        p_max_rounds: int = 3
    ) -> Dict[str, Any]:
        """
        샘플링 기반 후보 페어 마이닝 RPC 호출

        Args:
            p_last_src_id: 마지막 처리한 src ID (키셋 페이징)
            p_src_batch: 배치당 src 수 (기본 30)
            p_dst_sample: dst 샘플 크기 (기본 1200)
            p_k: src당 후보 수 (기본 15)
            p_lo: 하위 분위수 (기본 0.10)
            p_hi: 상위 분위수 (기본 0.35)
            p_seed: 결정론적 샘플링용 시드 (기본 42)
            p_max_rounds: 최대 재시도 횟수 (기본 3)

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
        await self._ensure_initialized()

        try:
            response = await self.client.rpc(
                "mine_candidate_pairs",
                {
                    "p_last_src_id": p_last_src_id,
                    "p_src_batch": p_src_batch,
                    "p_dst_sample": p_dst_sample,
                    "p_k": p_k,
                    "p_lo": p_lo,
                    "p_hi": p_hi,
                    "p_seed": p_seed,
                    "p_max_rounds": p_max_rounds
                }
            ).execute()

            if not response.data:
                raise Exception("RPC returned no data")

            result = response.data

            if result.get("success"):
                logger.info(
                    f"mine_candidate_pairs: "
                    f"{result.get('inserted_count')} pairs, "
                    f"{result.get('src_processed_count')} sources, "
                    f"{result.get('duration_ms')}ms"
                )
            else:
                logger.error(f"mine_candidate_pairs failed: {result.get('error')}")

            return result

        except Exception as e:
            logger.error(f"mine_candidate_pairs exception: {e}")
            return {
                "success": False,
                "error": str(e),
                "new_last_src_id": p_last_src_id
            }

    async def build_distribution_sketch(
        self,
        p_seed: int = 42,
        p_src_sample: int = 200,
        p_dst_sample: int = 500,
        p_rounds: int = 1,
        p_exclude_same_memo: bool = True,
        p_policy: str = "random_pairs"
    ) -> Dict[str, Any]:
        """
        전역 분포 스케치용 샘플 수집 RPC 호출

        Args:
            p_seed: 결정론적 샘플링용 시드 (기본 42)
            p_src_sample: src 샘플 크기 (기본 200)
            p_dst_sample: dst 샘플 크기 (기본 500)
            p_rounds: 샘플링 라운드 수 (기본 1)
            p_exclude_same_memo: 같은 메모 제외 여부 (기본 TRUE)
            p_policy: 샘플링 정책명 (기본 random_pairs)

        Returns:
            {
                "success": bool,
                "run_id": str,
                "inserted_samples": int,
                "total_thoughts": int,
                "coverage_estimate": float,
                "duration_ms": int
            }
        """
        await self._ensure_initialized()

        try:
            response = await self.client.rpc(
                "build_distribution_sketch",
                {
                    "p_seed": p_seed,
                    "p_src_sample": p_src_sample,
                    "p_dst_sample": p_dst_sample,
                    "p_rounds": p_rounds,
                    "p_exclude_same_memo": p_exclude_same_memo,
                    "p_policy": p_policy
                }
            ).execute()

            if not response.data:
                raise Exception("RPC returned no data")

            result = response.data

            if result.get("success"):
                logger.info(
                    f"build_distribution_sketch: "
                    f"{result.get('inserted_samples')} samples, "
                    f"run_id={result.get('run_id')}, "
                    f"{result.get('duration_ms')}ms"
                )
            else:
                logger.error(f"build_distribution_sketch failed: {result.get('error')}")

            return result

        except Exception as e:
            logger.error(f"build_distribution_sketch exception: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def calculate_distribution_from_sketch(
        self,
        p_run_id: str = None,
        p_sample_limit: int = 100000
    ) -> Dict[str, Any]:
        """
        샘플 기반 전역 분포 계산 RPC 호출

        Args:
            p_run_id: 특정 run의 샘플 사용 (NULL이면 최신)
            p_sample_limit: 최대 샘플 수 (기본 100,000)

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
        """
        await self._ensure_initialized()

        try:
            params = {"p_sample_limit": p_sample_limit}
            if p_run_id:
                params["p_run_id"] = p_run_id

            response = await self.client.rpc(
                "calculate_distribution_from_sketch",
                params
            ).execute()

            if not response.data:
                raise Exception("RPC returned no data")

            result = response.data

            if result.get("success"):
                logger.info(
                    f"calculate_distribution_from_sketch: "
                    f"{result.get('sample_count')} samples, "
                    f"cached={result.get('cached')}, "
                    f"{result.get('duration_ms')}ms"
                )
            else:
                logger.error(f"calculate_distribution_from_sketch failed: {result.get('error')}")

            return result

        except Exception as e:
            logger.error(f"calculate_distribution_from_sketch exception: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    # ============================================================
    # 마이닝 진행 상태 CRUD
    # ============================================================

    async def create_mining_progress(
        self,
        src_batch: int,
        dst_sample: int,
        k_per_src: int,
        p_lo: float,
        p_hi: float,
        max_rounds: int,
        seed: int
    ) -> Dict[str, Any]:
        """마이닝 진행 상태 레코드 생성"""
        await self._ensure_initialized()

        try:
            data = {
                "status": "in_progress",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "src_batch": src_batch,
                "dst_sample": dst_sample,
                "k_per_src": k_per_src,
                "p_lo": p_lo,
                "p_hi": p_hi,
                "max_rounds": max_rounds,
                "seed": seed
            }

            response = await (
                self.client.table("pair_mining_progress")
                .insert(data)
                .execute()
            )

            if response.data:
                logger.info(f"Created mining progress: id={response.data[0]['id']}")
                return response.data[0]
            else:
                raise Exception("Insert returned no data")

        except Exception as e:
            logger.error(f"Failed to create mining progress: {e}")
            raise

    async def update_mining_progress(
        self,
        progress_id: int,
        status: str,
        last_src_id: int = None,
        total_src_processed: int = None,
        total_pairs_inserted: int = None,
        avg_candidates_per_src: float = None,
        error_message: str = None
    ) -> Dict[str, Any]:
        """마이닝 진행 상태 업데이트"""
        await self._ensure_initialized()

        try:
            data = {
                "status": status,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }

            if last_src_id is not None:
                data["last_src_id"] = last_src_id
            if total_src_processed is not None:
                data["total_src_processed"] = total_src_processed
            if total_pairs_inserted is not None:
                data["total_pairs_inserted"] = total_pairs_inserted
            if avg_candidates_per_src is not None:
                data["avg_candidates_per_src"] = avg_candidates_per_src
            if error_message is not None:
                data["error_message"] = error_message

            if status == "completed":
                data["completed_at"] = datetime.now(timezone.utc).isoformat()

            response = await (
                self.client.table("pair_mining_progress")
                .update(data)
                .eq("id", progress_id)
                .execute()
            )

            if response.data:
                logger.debug(f"Updated mining progress: id={progress_id}, status={status}")
                return response.data[0]
            else:
                raise Exception(f"Mining progress {progress_id} not found")

        except Exception as e:
            logger.error(f"Failed to update mining progress: {e}")
            raise

    async def get_mining_progress(self) -> Optional[Dict[str, Any]]:
        """최신 마이닝 진행 상태 조회"""
        await self._ensure_initialized()

        try:
            response = await (
                self.client.table("pair_mining_progress")
                .select("*")
                .order("updated_at", desc=True)
                .limit(1)
                .maybe_single()
                .execute()
            )

            return response.data

        except Exception as e:
            logger.error(f"Failed to get mining progress: {e}")
            return None

    # ============================================================
    # Distance Table 조회 (레거시 - 향후 삭제 예정)
    # ============================================================

    async def get_candidates_from_distance_table(
        self,
        min_similarity: float,
        max_similarity: float
    ) -> List[dict]:
        """
        Distance Table에서 유사도 범위 내 후보 조회 (초고속).

        Performance: <0.1초 (vs v4 60초+)

        Security: 80% 범위 검증으로 비정상 요청 차단
        - Normal: p10_p40 (30% 범위) → ~48,000개 수집
        - Blocked: p0_p100 (100% 범위) → ValueError 발생

        구현 전략:
        0. 범위 검증 (80% 임계값)
        1. thought_pair_distances에서 유사도 범위 조회 (인덱스 활용, ~0.05초, 무제한)
        2. thought_units에서 claim, raw_note_id JOIN (~0.05초)
        3. 결과 조합 (Python 메모리 연산)

        Args:
            min_similarity: 최소 유사도 [0, 1] (예: 0.057)
            max_similarity: 최대 유사도 [0, 1] (예: 0.093)

        Returns:
            List[dict]: [
                {
                    "thought_a_id": int,
                    "thought_b_id": int,
                    "thought_a_claim": str,
                    "thought_b_claim": str,
                    "similarity": float,
                    "raw_note_id_a": str,
                    "raw_note_id_b": str
                }
            ]

        Raises:
            ValueError: 범위가 80%를 초과하는 경우
            Exception: DB 조회 실패 시
        """
        await self._ensure_initialized()

        # Step 0: 범위 검증 (80% 임계값)
        similarity_range = max_similarity - min_similarity
        if similarity_range > 0.8:
            error_msg = (
                f"Similarity range too wide: {similarity_range:.1%} > 80%. "
                f"Range [{min_similarity:.3f}, {max_similarity:.3f}] is likely an error. "
                f"Normal strategies use 30-40% range (e.g., p10_p40, p30_p60)."
            )
            logger.error(f"Range validation failed: {error_msg}")
            raise ValueError(error_msg)

        logger.info(
            f"Querying distance table: "
            f"range=[{min_similarity:.3f}, {max_similarity:.3f}] "
            f"({similarity_range:.1%}), no limit"
        )

        try:
            start_time = time.time()

            # Step 1: 유사도 범위 조회 (페이징 처리)
            # Supabase REST API는 기본적으로 1,000개만 반환하므로 페이징 필요
            # 안전 상한선: 100,000개 (80% 범위 검증으로 대부분 차단됨)
            pairs = []
            page_size = 1000  # Supabase 기본 limit
            max_total = 100000  # 안전 상한선
            offset = 0

            while len(pairs) < max_total:
                page_response = await (
                    self.client.table("thought_pair_distances")
                    .select("thought_a_id, thought_b_id, similarity")
                    .gte("similarity", min_similarity)
                    .lte("similarity", max_similarity)
                    .order("similarity", desc=False)  # 낮은 유사도부터
                    .range(offset, offset + page_size - 1)  # 페이징
                    .execute()
                )

                page_data = page_response.data
                if not page_data:
                    # 더 이상 데이터 없음
                    break

                pairs.extend(page_data)

                # 마지막 페이지인 경우 종료
                if len(page_data) < page_size:
                    break

                offset += page_size

                # 로그 (2페이지 이상일 때만)
                if offset > page_size:
                    logger.info(f"  Fetched {len(pairs)} pairs so far (offset: {offset})...")

            step1_duration = time.time() - start_time

            if not pairs:
                logger.info(
                    f"No pairs found in similarity range "
                    f"[{min_similarity:.3f}, {max_similarity:.3f}]"
                )
                return []

            logger.info(
                f"Step 1: Found {len(pairs)} pairs in {step1_duration:.2f}s "
                f"({len(pairs)//page_size + 1} pages)"
            )

            # Step 2: thought_units에서 claim, raw_note_id JOIN
            step2_start = time.time()

            # 모든 thought ID 수집 (중복 제거)
            thought_ids = set()
            for p in pairs:
                thought_ids.add(p["thought_a_id"])
                thought_ids.add(p["thought_b_id"])

            # 배치 조회 (IN 연산)
            thoughts_response = await (
                self.client.table("thought_units")
                .select("id, claim, raw_note_id")
                .in_("id", list(thought_ids))
                .execute()
            )

            # thought_id → {claim, raw_note_id} 매핑
            thought_map = {
                t["id"]: {
                    "claim": t["claim"],
                    "raw_note_id": t["raw_note_id"]
                }
                for t in thoughts_response.data
            }

            step2_duration = time.time() - step2_start

            logger.info(
                f"Step 2: Retrieved {len(thought_map)} thought details in {step2_duration:.2f}s"
            )

            # Step 3: 결과 조합 (Python 메모리 연산)
            step3_start = time.time()

            result = []
            for p in pairs:
                a_id = p["thought_a_id"]
                b_id = p["thought_b_id"]

                # thought_map에 없는 경우 스킵 (데이터 정합성 문제)
                if a_id not in thought_map or b_id not in thought_map:
                    logger.warning(
                        f"Missing thought data: thought_a_id={a_id}, thought_b_id={b_id}"
                    )
                    continue

                result.append({
                    "thought_a_id": a_id,
                    "thought_b_id": b_id,
                    "thought_a_claim": thought_map[a_id]["claim"],
                    "thought_b_claim": thought_map[b_id]["claim"],
                    "similarity": p["similarity"],
                    "raw_note_id_a": thought_map[a_id]["raw_note_id"],
                    "raw_note_id_b": thought_map[b_id]["raw_note_id"]
                })

            step3_duration = time.time() - step3_start
            total_duration = time.time() - start_time

            logger.info(
                f"Step 3: Combined {len(result)} pairs in {step3_duration:.2f}s. "
                f"Total duration: {total_duration:.2f}s"
            )

            return result

        except Exception as e:
            logger.error(f"Failed to get candidates from distance table: {e}")
            raise


# 싱글톤 인스턴스
_supabase_service: Optional[SupabaseService] = None


def get_supabase_service() -> SupabaseService:
    """
    Supabase 서비스 싱글톤 인스턴스 반환.

    FastAPI Depends에서 사용.
    """
    global _supabase_service
    if _supabase_service is None:
        _supabase_service = SupabaseService()
    return _supabase_service
