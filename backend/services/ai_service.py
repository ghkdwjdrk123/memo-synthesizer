"""
AI service integration for embeddings and content generation.
"""

import json
import logging
import re
from typing import List, Dict, Any, Optional

from openai import OpenAI
from anthropic import Anthropic

from config import settings
from schemas.normalized import ThoughtExtractionResult
from schemas.zk import ThoughtPairCandidate, PairScoringResult

logger = logging.getLogger(__name__)


def safe_json_parse(content: str) -> Optional[dict | list]:
    """
    Robust JSON parsing with multiple fallback strategies.

    Handles common LLM output issues:
    - Markdown code blocks
    - Extra text before/after JSON
    - Trailing commas
    - Unescaped newlines in strings

    Args:
        content: Raw string that may contain JSON

    Returns:
        Parsed JSON as dict/list, or None if all strategies fail
    """
    if not content:
        return None

    # Stage 1: Direct parse
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # Stage 2: Extract from markdown code block
    code_block = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', content)
    if code_block:
        try:
            return json.loads(code_block.group(1))
        except json.JSONDecodeError:
            pass

    # Stage 3: Find JSON-like structure (first { to last } or [ to ])
    json_match = re.search(r'[\[{][\s\S]*[\]}]', content)
    if json_match:
        json_str = json_match.group()

        # Try direct parse first
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

        # Stage 4: Clean up common issues
        cleaned = json_str

        # Remove trailing commas before } or ]
        cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)

        # Replace unescaped newlines inside strings with escaped version
        # This is tricky - we need to be careful not to break actual JSON structure
        # Only fix newlines that appear to be inside string values
        def fix_string_newlines(match):
            s = match.group(0)
            # Replace actual newlines with \n escape sequence
            s = s.replace('\n', '\\n').replace('\r', '\\r')
            return s

        # Match string contents (between quotes, handling escaped quotes)
        cleaned = re.sub(r'"(?:[^"\\]|\\.)*"', fix_string_newlines, cleaned)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Stage 5: Try line-by-line repair for broken strings
        try:
            # Sometimes the issue is a string that spans multiple lines
            # Try to find and fix those
            lines = cleaned.split('\n')
            repaired_lines = []
            in_string = False

            for line in lines:
                # Count unescaped quotes to track string state
                quote_count = len(re.findall(r'(?<!\\)"', line))

                if in_string:
                    # We're continuing a string from previous line
                    repaired_lines[-1] = repaired_lines[-1].rstrip('"') + '\\n' + line.lstrip()
                    if quote_count % 2 == 1:
                        in_string = False
                else:
                    repaired_lines.append(line)
                    if quote_count % 2 == 1:
                        in_string = True

            repaired = '\n'.join(repaired_lines)
            return json.loads(repaired)
        except (json.JSONDecodeError, IndexError):
            pass

    return None


class AIService:
    """Service for AI operations using OpenAI and Anthropic."""

    def __init__(self):
        """Initialize AI clients."""
        self.openai_client = OpenAI(api_key=settings.openai_api_key)
        self.anthropic_client = Anthropic(api_key=settings.anthropic_api_key)

    async def create_embedding(self, text: str, model: str = "text-embedding-3-small") -> Dict[str, Any]:
        """
        Create text embedding using OpenAI.

        Args:
            text: Text to embed
            model: Embedding model to use

        Returns:
            Dict containing embedding and metadata
        """
        try:
            response = self.openai_client.embeddings.create(
                input=text,
                model=model
            )

            embedding = response.data[0].embedding

            return {
                "success": True,
                "embedding": embedding,
                "dimension": len(embedding),
                "model": model,
                "tokens_used": response.usage.total_tokens
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__
            }

    async def generate_content_with_claude(
        self,
        prompt: str,
        system_message: str = "당신은 도움이 되는 AI 어시스턴트입니다.",
        model: str = "claude-sonnet-4-5-20250929",
        max_tokens: int = 1024,
        temperature: float = 1.0
    ) -> Dict[str, Any]:
        """
        Generate content using Anthropic Claude.

        Args:
            prompt: User prompt
            system_message: System message for Claude
            model: Claude model to use
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature (0.0-1.0)

        Returns:
            Dict containing generated content and metadata
        """
        try:
            message = self.anthropic_client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_message,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            content = message.content[0].text

            return {
                "success": True,
                "content": content,
                "model": model,
                "tokens_used": {
                    "input": message.usage.input_tokens,
                    "output": message.usage.output_tokens
                },
                "stop_reason": message.stop_reason
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__
            }

    async def extract_thoughts(
        self, title: str, content: str
    ) -> ThoughtExtractionResult:
        """
        RAW 메모에서 사고 단위 추출 (Claude 3.5 Sonnet).

        Args:
            title: 메모 제목
            content: 메모 본문

        Returns:
            ThoughtExtractionResult (1-5개의 ThoughtUnit)

        Raises:
            ValueError: JSON 파싱 실패 시
            Exception: API 호출 실패 시
        """
        system_message = """당신은 메모에서 독립적인 사고 단위를 추출하는 전문가입니다.

각 사고 단위는 다음 조건을 만족해야 합니다:
1. 독립적으로 이해 가능한 완결된 생각
2. 하나의 명확한 주장(claim)을 포함
3. 필요시 맥락(context)을 간단히 제공
4. 10-500자 길이

중요: 메모 전체를 그대로 반환하지 말고, 의미 있는 사고 단위로 분해하세요."""

        prompt = f"""다음 메모에서 1-5개의 독립적인 사고 단위를 추출하세요.

제목: {title}

본문:
{content}

다음 JSON 형식으로 응답하세요:
{{
  "thoughts": [
    {{
      "claim": "핵심 주장/아이디어 (10-500자)",
      "context": "맥락/배경 정보 (선택, 최대 200자)"
    }}
  ]
}}

JSON만 반환하고, 다른 설명은 포함하지 마세요."""

        try:
            result = await self.generate_content_with_claude(
                prompt=prompt,
                system_message=system_message,
                model="claude-sonnet-4-5-20250929",  # 가격 대비 성능 최고
                max_tokens=2000,
            )

            if not result["success"]:
                raise Exception(
                    f"Claude API error: {result.get('error', 'Unknown error')}"
                )

            content_text = result["content"].strip()

            # JSON 추출 (```json ... ``` 제거)
            if content_text.startswith("```json"):
                content_text = content_text[7:]
            if content_text.startswith("```"):
                content_text = content_text[3:]
            if content_text.endswith("```"):
                content_text = content_text[:-3]
            content_text = content_text.strip()

            # JSON 파싱
            try:
                data = json.loads(content_text)
            except json.JSONDecodeError as e:
                logger.error(f"JSON parse error: {e}\nContent: {content_text}")
                raise ValueError(f"Invalid JSON response from Claude: {e}")

            # Pydantic 검증
            extraction_result = ThoughtExtractionResult.model_validate(data)

            logger.info(
                f"Extracted {len(extraction_result.thoughts)} thoughts from note: {title[:50]}"
            )
            return extraction_result

        except Exception as e:
            logger.error(f"Failed to extract thoughts: {e}")
            raise

    async def recommend_topics(
        self,
        memos: List[Dict[str, Any]],
        max_topics: int = 5
    ) -> Dict[str, Any]:
        """
        Recommend writing topics based on memos using Claude.

        Args:
            memos: List of memo dictionaries with 제목, 본문, 키워드
            max_topics: Maximum number of topics to recommend

        Returns:
            Dict containing recommended topics
        """
        # 메모 내용을 텍스트로 변환
        memo_texts = []
        for memo in memos:
            text = f"제목: {memo.get('제목', '')}\n"
            text += f"본문: {memo.get('본문', '')}\n"
            keywords = memo.get('키워드', [])
            if keywords:
                text += f"키워드: {', '.join(keywords)}\n"
            memo_texts.append(text)

        combined_text = "\n\n---\n\n".join(memo_texts)

        prompt = f"""다음은 사용자가 작성한 메모들입니다:

{combined_text}

이 메모들을 분석하여, 사용자가 글을 쓸 만한 주제를 {max_topics}개 추천해주세요.
각 추천 주제는 다음 형식으로 작성해주세요:

1. [주제 제목]
   - 이유: [왜 이 주제를 추천하는지]
   - 연결된 키워드: [관련 키워드들]

추천 주제:"""

        return await self.generate_content_with_claude(
            prompt=prompt,
            system_message="당신은 사용자의 메모를 분석하여 글감을 추천하는 전문가입니다.",
            max_tokens=2048
        )

    async def score_pairs(
        self,
        candidates: List[ThoughtPairCandidate],
        top_n: int = 5,
        max_pairs_per_batch: int = 10
    ) -> PairScoringResult:
        """
        여러 후보 쌍의 논리적 확장 가능성 평가.

        Args:
            candidates: 유사도 범위 내 후보 쌍 목록
            top_n: 상위 몇 개를 선택할지 (사용 안 함, 참고만)
            max_pairs_per_batch: 한 번에 평가할 최대 쌍 개수 (기본 10개)

        Returns:
            PairScoringResult: 각 쌍의 점수 및 연결 이유

        Raises:
            ValueError: JSON 파싱 실패 시
            Exception: API 호출 실패 시
        """
        # 배치 처리: 후보가 많으면 분할
        if len(candidates) > max_pairs_per_batch:
            logger.info(
                f"Batching {len(candidates)} pairs into chunks of {max_pairs_per_batch}"
            )
            all_scores = []
            for i in range(0, len(candidates), max_pairs_per_batch):
                batch = candidates[i:i + max_pairs_per_batch]
                batch_result = await self._score_pairs_batch(batch)
                all_scores.extend(batch_result.pair_scores)

            return PairScoringResult(pair_scores=all_scores)

        return await self._score_pairs_batch(candidates)

    async def _score_pairs_batch(
        self,
        candidates: List[ThoughtPairCandidate],
        max_retries: int = 2
    ) -> PairScoringResult:
        """
        단일 배치의 후보 쌍 평가 (내부 메서드).

        낮은 유사도(0.05-0.35)의 서로 다른 도메인 아이디어 쌍을 평가합니다.
        목표: "억지 연결"을 걸러내고, 진정으로 창의적이고 신선한 연결만 선택

        Args:
            candidates: 평가할 후보 쌍 목록 (권장 10개 이하)
            max_retries: 파싱 실패 시 재시도 횟수

        Returns:
            PairScoringResult: 각 쌍의 점수 및 연결 이유
        """
        system_message = """당신은 서로 다른 도메인의 아이디어 간 창의적 연결 가능성을 평가하는 전문가입니다.

중요 배경:
- 입력되는 쌍들은 의도적으로 유사도가 낮은 조합입니다 (서로 다른 주제/도메인)
- 목표: 억지 연결을 걸러내고, 통찰력 있는 연결만 높은 점수를 부여

## 점수 기준 (0-100)
- 0-40: 억지 연결, 무의미한 조합 (예: "커피" + "양자역학" - 연결점이 거의 없음)
- 41-64: 연결 가능하나 평범하거나 표면적 (예: "운동" + "건강" - 뻔한 연결)
- 65-85: 신선하고 예상 밖의 연결 (예: "게임 난이도" + "교육 최적 도전" - 플로우 이론 공유)
- 86-100: 매우 창의적이고 통찰력 있는 연결 (예: "정원 가꾸기" + "소프트웨어 리팩토링" - 점진적 개선의 철학)

## 평가 원칙
- 단순 단어 유사성만 있으면 → 낮은 점수
- 비유나 메타포 수준의 연결만 → 중간 점수
- 근본 원리나 구조의 유사성 발견 → 높은 점수
- 전혀 무관하고 연결점이 없는 조합 → 매우 낮은 점수

중요: JSON 형식을 정확히 지키세요. connection_reason에 줄바꿈을 넣지 마세요."""

        # 후보 쌍 목록 텍스트 생성
        pairs_text = "\n".join([
            f"{i+1}. thought_a_id={c.thought_a_id}, thought_b_id={c.thought_b_id}\n"
            f"   - claim_a: {c.thought_a_claim}\n"
            f"   - claim_b: {c.thought_b_claim}\n"
            f"   - similarity: {c.similarity_score:.2f}"
            for i, c in enumerate(candidates)
        ])

        prompt = f"""다음은 낮은 유사도(서로 다른 도메인)의 아이디어 쌍들입니다.
각 쌍의 창의적 연결 가능성을 평가하세요.

## 후보 쌍 목록
{pairs_text}

## 예시
입력: claim_a="게임에서 적절한 난이도가 몰입감을 높인다", claim_b="학습에서 최적의 도전이 성장을 촉진한다"
출력:
```json
{{"thought_a_id": 1, "thought_b_id": 2, "logical_expansion_score": 78, "connection_reason": "두 아이디어 모두 칙센트미하이의 플로우 이론에 기반하며, 도전과 능력의 균형이라는 근본 원리를 공유한다"}}
```

## 출력 형식 (이 JSON 구조를 정확히 따르세요)
```json
{{
  "pair_scores": [
    {{
      "thought_a_id": 1,
      "thought_b_id": 2,
      "logical_expansion_score": 75,
      "connection_reason": "두 아이디어의 창의적 연결 이유를 구체적으로 설명"
    }}
  ]
}}
```

## 규칙
- 모든 후보 쌍에 대해 평가 결과를 포함하세요
- connection_reason은 10-300자, 한 줄로 작성 (줄바꿈 금지)
- logical_expansion_score는 0-100 정수입니다
- 따옴표(")를 connection_reason 안에 사용하지 마세요

JSON만 출력하세요. 다른 텍스트는 포함하지 마세요."""

        last_error = None
        raw_content = None

        for attempt in range(max_retries + 1):
            try:
                result = await self.generate_content_with_claude(
                    prompt=prompt,
                    system_message=system_message,
                    model="claude-sonnet-4-5-20250929",
                    max_tokens=2000,
                    temperature=1.0,
                )

                if not result["success"]:
                    raise Exception(
                        f"Claude API error: {result.get('error', 'Unknown error')}"
                    )

                raw_content = result["content"]

                # 상세 로깅 (디버깅용)
                logger.debug(
                    f"Claude response (attempt {attempt + 1}): "
                    f"length={len(raw_content)}, "
                    f"tokens_used={result.get('tokens_used', {})}"
                )

                # safe_json_parse 사용
                data = safe_json_parse(raw_content)

                if data is None:
                    # 파싱 실패 시 원본 내용 로깅 (처음 500자 + 마지막 500자)
                    content_preview = raw_content[:500]
                    if len(raw_content) > 1000:
                        content_preview += f"\n...[{len(raw_content) - 1000} chars omitted]...\n"
                        content_preview += raw_content[-500:]
                    elif len(raw_content) > 500:
                        content_preview += raw_content[500:]

                    logger.warning(
                        f"JSON parse failed (attempt {attempt + 1}/{max_retries + 1}). "
                        f"Content preview:\n{content_preview}"
                    )

                    if attempt < max_retries:
                        # 재시도 시 더 명시적인 프롬프트 사용
                        prompt = self._make_simplified_score_prompt(candidates)
                        continue

                    raise ValueError(
                        f"Failed to parse JSON after {max_retries + 1} attempts. "
                        f"Response length: {len(raw_content)}"
                    )

                # Pydantic 검증
                scoring_result = PairScoringResult.model_validate(data)

                # 통계 로깅
                scores = [ps.logical_expansion_score for ps in scoring_result.pair_scores]
                avg_score = sum(scores) / len(scores) if scores else 0

                logger.info(
                    f"Scored {len(scoring_result.pair_scores)} pairs, "
                    f"avg_score={avg_score:.1f}, "
                    f"min={min(scores) if scores else 0}, "
                    f"max={max(scores) if scores else 0}"
                )

                return scoring_result

            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning(
                        f"score_pairs attempt {attempt + 1} failed: {e}. Retrying..."
                    )
                    continue
                break

        # 모든 재시도 실패
        logger.error(
            f"Failed to score pairs after {max_retries + 1} attempts. "
            f"Last error: {last_error}"
        )
        if raw_content:
            logger.error(f"Last raw content length: {len(raw_content)}")

        raise last_error or ValueError("Unknown error in score_pairs")

    def _make_simplified_score_prompt(
        self,
        candidates: List[ThoughtPairCandidate]
    ) -> str:
        """
        파싱 실패 후 재시도용 단순화된 프롬프트 생성.

        Args:
            candidates: 평가할 후보 쌍 목록

        Returns:
            단순화된 프롬프트 문자열
        """
        pairs_json = []
        for c in candidates:
            pairs_json.append({
                "a_id": c.thought_a_id,
                "b_id": c.thought_b_id,
                "a": c.thought_a_claim[:100],  # 축약
                "b": c.thought_b_claim[:100]
            })

        return f"""아래 쌍들을 평가하세요. JSON만 출력하세요.

입력: {json.dumps(pairs_json, ensure_ascii=False)}

출력 형식:
{{"pair_scores": [{{"thought_a_id": 1, "thought_b_id": 2, "logical_expansion_score": 75, "connection_reason": "이유 (한 줄, 300자 이내)"}}]}}

중요:
- connection_reason에 줄바꿈, 따옴표 금지
- JSON만 출력 (설명 금지)"""


def get_ai_service() -> AIService:
    """
    Get AIService instance.

    Returns:
        AIService instance
    """
    return AIService()
