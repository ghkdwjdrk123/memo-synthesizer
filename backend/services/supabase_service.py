"""
Supabase 서비스.

PostgreSQL + pgvector를 사용한 데이터 CRUD.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from uuid import UUID

from supabase import create_async_client, AsyncClient

from config import settings
from schemas.raw import RawNote, RawNoteCreate
from schemas.normalized import ThoughtUnitCreate
from schemas.zk import ThoughtPairCreate

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
        모든 RAW note의 ID 목록 조회 (메모리 절약).

        Returns:
            UUID 목록
        """
        await self._ensure_initialized()

        try:
            response = await self.client.table("raw_notes").select("id").execute()

            ids = [row["id"] for row in response.data]
            logger.info(f"Retrieved {len(ids)} raw note IDs")
            return ids

        except Exception as e:
            logger.error(f"Failed to get raw note IDs: {e}")
            raise

    async def get_raw_notes_by_ids(self, note_ids: List[str]) -> List[dict]:
        """
        ID 목록으로 RAW notes 조회 (배치별 full content 로드).

        Args:
            note_ids: UUID 목록

        Returns:
            RAW note 데이터 목록
        """
        await self._ensure_initialized()

        try:
            response = await (
                self.client.table("raw_notes")
                .select("*")
                .in_("id", note_ids)
                .execute()
            )

            logger.info(f"Retrieved {len(response.data)} raw notes by IDs")
            return response.data

        except Exception as e:
            logger.error(f"Failed to get raw notes by IDs: {e}")
            raise

    async def get_raw_note_count(self) -> int:
        """RAW notes 총 개수 조회."""
        await self._ensure_initialized()

        try:
            response = await (
                self.client.table("raw_notes")
                .select("id", count="exact")
                .execute()
            )

            count = response.count if response.count else 0
            logger.info(f"Total raw notes: {count}")
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
        limit: int = 20
    ) -> List[dict]:
        """
        Stored Procedure를 호출하여 후보 페어 조회 (낮은 유사도 = 약한 연결).

        Args:
            min_similarity: 최소 유사도 (기본 0.05, 낮은 유사도 = 서로 다른 아이디어)
            max_similarity: 최대 유사도 (기본 0.35, 약한 연결 = 창의적 확장 가능)
            limit: 반환할 최대 개수 (기본 20)

        Returns:
            후보 쌍 목록 (thought_a_id, thought_b_id, similarity_score, thought_a_claim, thought_b_claim)

        Raises:
            Exception: Stored Procedure 호출 실패 시
        """
        await self._ensure_initialized()

        try:
            response = await self.client.rpc(
                "find_similar_pairs",
                {
                    "min_sim": min_similarity,
                    "max_sim": max_similarity,
                    "lim": limit
                }
            ).execute()

            logger.info(
                f"Found {len(response.data)} candidate pairs with weak connections "
                f"(similarity: {min_similarity:.2f}-{max_similarity:.2f})"
            )
            return response.data

        except Exception as e:
            error_msg = str(e)
            if "function" in error_msg.lower() and "does not exist" in error_msg.lower():
                logger.error(
                    "Stored procedure 'find_similar_pairs' not found. "
                    "Please run docs/supabase_setup.sql first"
                )
                raise Exception(
                    "Stored procedure 'find_similar_pairs' not found. "
                    "Please run docs/supabase_setup.sql first"
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
    ) -> tuple[List[str], List[str]]:
        """
        Compare Notion pages with DB using server-side RPC.

        Uses PostgreSQL function for efficient change detection.
        Falls back to full table scan if RPC fails.

        Args:
            notion_pages: List of page metadata from Notion API
                Each page must have: id, last_edited_time

        Returns:
            Tuple of (new_page_ids, updated_page_ids)

        Performance:
            - RPC mode: ~150ms (constant time, scales to 100k pages)
            - Fallback mode: ~110ms (current size)
            - Network: Only changed pages (0.5KB vs 60KB)

        Example:
            >>> pages = [{"id": "abc", "last_edited_time": "2024-01-15T14:30:00.000Z"}]
            >>> new, updated = await service.get_pages_to_fetch(pages)
            >>> print(f"New: {len(new)}, Updated: {len(updated)}")
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
            return [], []

        logger.info(f"Change detection: checking {len(pages_json)} pages via RPC (sample: {[p['id'] for p in pages_json[:3]]})")

        # Try RPC change detection (Solution 3)
        try:
            import time
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

            # Validate types
            if not isinstance(new_page_ids, list):
                raise ValueError(f"Invalid type for new_page_ids: {type(new_page_ids)}")
            if not isinstance(updated_page_ids, list):
                raise ValueError(f"Invalid type for updated_page_ids: {type(updated_page_ids)}")

            # Add force_new pages
            new_page_ids.extend(force_new_ids)

            # Validate UUIDs
            import re
            UUID_PATTERN = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)

            for page_id in new_page_ids + updated_page_ids:
                if not UUID_PATTERN.match(page_id):
                    raise ValueError(f"Invalid UUID format: {page_id}")

            logger.info(
                f"✅ RPC change detection completed in {elapsed:.2f}s: "
                f"{len(new_page_ids)} new, {len(updated_page_ids)} updated, "
                f"{result.get('unchanged_count', len(pages_json) - len(new_page_ids) - len(updated_page_ids))} unchanged"
            )

            return new_page_ids, updated_page_ids

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

                logger.info(
                    f"✅ Fallback completed: {len(new_ids)} new, {len(updated_ids)} updated, "
                    f"{len(page_map) - len(new_ids) - len(updated_ids)} unchanged"
                )

                return new_ids, updated_ids

            except Exception as fallback_error:
                logger.error(f"❌ Fallback also failed: {fallback_error}, treating all as new (last resort)")

                # Last resort: treat all as new
                all_ids = [p["id"] for p in pages_json] + force_new_ids
                return all_ids, []

    async def validate_rpc_function_exists(self) -> bool:
        """
        Check if RPC function is deployed in Supabase.

        Returns:
            bool: True if function exists and works, False otherwise
        """
        try:
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
