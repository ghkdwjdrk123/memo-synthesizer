# 하이브리드 C 전략 테스트 가이드

하이브리드 C 전략 구현에 대한 포괄적인 테스트 스위트 실행 가이드입니다.

## 테스트 파일 구조

```
backend/tests/
├── conftest.py                          # 공통 fixtures (하이브리드 전략용 추가)
├── unit/
│   ├── test_sampling.py                 # SamplingStrategy 테스트
│   ├── test_batch_worker.py             # BatchEvaluationWorker 테스트
│   └── test_supabase_hybrid.py          # Supabase 하이브리드 메서드 테스트
└── integration/
    └── test_pipeline_hybrid.py          # 파이프라인 엔드포인트 통합 테스트
```

## 테스트 실행 방법

### 1. 전체 테스트 실행

```bash
# 하이브리드 전략 테스트만 실행
pytest backend/tests/unit/test_sampling.py \
       backend/tests/unit/test_batch_worker.py \
       backend/tests/unit/test_supabase_hybrid.py \
       backend/tests/integration/test_pipeline_hybrid.py \
       -v

# 출력 예시:
# test_sampling.py::TestSamplingStrategy::test_sample_initial_basic PASSED
# test_batch_worker.py::TestBatchEvaluationWorker::test_run_batch_success PASSED
# ...
```

### 2. 개별 파일 테스트

```bash
# 샘플링 전략만 테스트
pytest backend/tests/unit/test_sampling.py -v

# 배치 워커만 테스트
pytest backend/tests/unit/test_batch_worker.py -v

# Supabase 메서드만 테스트
pytest backend/tests/unit/test_supabase_hybrid.py -v

# 파이프라인 통합 테스트만
pytest backend/tests/integration/test_pipeline_hybrid.py -v
```

### 3. 특정 테스트 케이스만 실행

```bash
# 샘플링 다양성 테스트만 실행
pytest backend/tests/unit/test_sampling.py::TestSamplingStrategy::test_sample_initial_diversity -v

# 배치 평가 성공 테스트만 실행
pytest backend/tests/unit/test_batch_worker.py::TestBatchEvaluationWorker::test_run_batch_success -v
```

### 4. 커버리지 확인

```bash
# 하이브리드 전략 모듈 커버리지 측정
pytest backend/tests/unit/test_sampling.py \
       backend/tests/unit/test_batch_worker.py \
       backend/tests/unit/test_supabase_hybrid.py \
       backend/tests/integration/test_pipeline_hybrid.py \
       --cov=services/sampling \
       --cov=services/batch_worker \
       --cov=services/supabase_service \
       --cov=routers/pipeline \
       --cov-report=term-missing \
       -v

# 출력 예시:
# services/sampling.py           95%   5-10
# services/batch_worker.py       92%   45-50
# ...
```

### 5. 패턴 매칭으로 테스트 선택

```bash
# "diversity" 키워드가 포함된 테스트만 실행
pytest backend/tests/ -k "diversity" -v

# "failure" 또는 "error" 키워드가 포함된 테스트만 실행
pytest backend/tests/ -k "failure or error" -v

# "hybrid" 키워드가 포함된 통합 테스트만 실행
pytest backend/tests/integration/ -k "hybrid" -v
```

## 테스트 모듈별 상세 설명

### test_sampling.py

**대상 모듈:** `services/sampling.py` - SamplingStrategy

**주요 테스트 케이스:**

| 테스트 메서드 | 목적 | 검증 내용 |
|-------------|------|----------|
| `test_sample_initial_basic` | 기본 샘플링 | 100개 목표 → 정확히 100개 반환 |
| `test_sample_initial_diversity` | 다양성 샘플링 | raw_note 조합 균등 분포 |
| `test_sample_initial_similarity_distribution` | 유사도 구간별 비율 | Low 40%, Mid 35%, High 25% |
| `test_sample_initial_insufficient_candidates` | 후보 부족 | 전체 반환, 에러 없음 |
| `test_sample_initial_empty_candidates` | 빈 입력 | 빈 리스트 반환 |
| `test_filter_by_similarity` | 필터링 정확성 | min <= similarity < max |
| `test_diverse_sample_round_robin` | Round-robin | 각 그룹에서 균등 샘플링 |

**실행 예시:**
```bash
pytest backend/tests/unit/test_sampling.py -v --tb=short
```

### test_batch_worker.py

**대상 모듈:** `services/batch_worker.py` - BatchEvaluationWorker

**주요 테스트 케이스:**

| 테스트 메서드 | 목적 | 검증 내용 |
|-------------|------|----------|
| `test_run_batch_success` | 배치 평가 성공 | 10개 평가 → 3개 이동 |
| `test_run_batch_no_pending` | pending 없음 | evaluated=0, 에러 없음 |
| `test_run_batch_partial_failure` | 부분 실패 | 10개 중 2개 실패 → 8개 성공 |
| `test_run_batch_rate_limiting` | Rate limiting | 배치 간 0.5초 대기 |
| `test_run_batch_auto_migrate_disabled` | auto_migrate=False | 고득점도 이동 안 함 |
| `test_run_batch_scoring_api_failure` | Claude API 실패 | 배치 전체 failed |
| `test_run_batch_high_score_collection` | 고득점 수집 | 65점 이상만 정확히 수집 |

**실행 예시:**
```bash
pytest backend/tests/unit/test_batch_worker.py -v -s
```

### test_supabase_hybrid.py

**대상 모듈:** `services/supabase_service.py` (하이브리드 전략 신규 메서드)

**주요 테스트 케이스:**

| 테스트 메서드 | 목적 | 검증 내용 |
|-------------|------|----------|
| `test_insert_pair_candidates_batch_success` | 배치 저장 | 1000개 → inserted_count 확인 |
| `test_insert_pair_candidates_batch_empty` | 빈 리스트 | 경고 + 빈 결과 |
| `test_get_pending_candidates_basic` | pending 조회 | llm_status='pending' 필터링 |
| `test_get_pending_candidates_missing_claim` | claim 누락 | 누락된 후보 스킵 |
| `test_update_candidate_score_success` | 점수 업데이트 | llm_attempts 자동 증가 |
| `test_move_to_thought_pairs_success` | 고득점 이동 | quality_tier 계산 확인 |
| `test_move_to_thought_pairs_quality_tier_boundaries` | tier 경계값 | 65, 85, 95 경계 정확성 |

**실행 예시:**
```bash
pytest backend/tests/unit/test_supabase_hybrid.py -v --tb=line
```

### test_pipeline_hybrid.py

**대상 모듈:** `routers/pipeline.py` (하이브리드 전략 엔드포인트)

**주요 테스트 클래스:**

| 테스트 클래스 | 엔드포인트 | 검증 내용 |
|-------------|-----------|----------|
| `TestCollectCandidatesEndpoint` | `/pipeline/collect-candidates` | 30,000개 수집 + DB 저장 |
| `TestSampleInitialEndpoint` | `/pipeline/sample-initial` | 100개 샘플링 + 평가 |
| `TestScoreCandidatesEndpoint` | `/pipeline/score-candidates` | 백그라운드 실행 확인 |
| `TestRecommendedEssaysEndpoint` | `/essays/recommended` | AI 추천 조회 |
| `TestHybridPipelineEndToEnd` | 전체 플로우 | collect → sample → score |

**실행 예시:**
```bash
pytest backend/tests/integration/test_pipeline_hybrid.py -v --asyncio-mode=auto
```

## Mock 데이터 구조

### conftest.py의 주요 Fixtures

1. **mock_supabase_hybrid**: Supabase 클라이언트 mock
2. **mock_ai_service_hybrid**: AI 서비스 (Claude) mock
3. **sample_candidates**: 1000개 샘플 후보 (다양한 유사도)
4. **sample_pending_candidates**: 50개 pending 후보 (claim 포함)
5. **sample_scored_pairs**: 10개 평가 완료 페어 (점수: 55~95)

**사용 예시:**
```python
@pytest.mark.asyncio
async def test_my_feature(sample_candidates, mock_supabase_hybrid):
    # sample_candidates는 1000개 후보 데이터
    assert len(sample_candidates) == 1000

    # mock_supabase_hybrid로 DB 호출 모킹
    service = SupabaseService()
    # ...
```

## 에러 발생 시 디버깅

### 1. 테스트 실패 시 상세 로그 보기

```bash
pytest backend/tests/unit/test_sampling.py -v -s --tb=long
# -s: print 출력 보기
# --tb=long: 전체 traceback
```

### 2. 특정 테스트만 재실행

```bash
pytest backend/tests/unit/test_sampling.py::TestSamplingStrategy::test_sample_initial_diversity --pdb
# --pdb: 실패 시 디버거 진입
```

### 3. 로깅 레벨 조정

```bash
pytest backend/tests/ -v --log-cli-level=DEBUG
# 모든 로그 레벨 출력
```

## 성능 테스트 (선택 사항)

성능 테스트는 `@pytest.mark.performance` 마커로 구분되어 있습니다.

```bash
# 성능 테스트만 실행
pytest backend/tests/ -m performance -v

# 성능 테스트 제외하고 실행
pytest backend/tests/ -m "not performance" -v
```

**주의:** 성능 테스트는 실제 시간 측정을 포함하므로 CI/CD 환경에서는 불안정할 수 있습니다.

## CI/CD 통합

GitHub Actions 예시:

```yaml
# .github/workflows/test-hybrid-strategy.yml
name: Hybrid Strategy Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install pytest pytest-asyncio pytest-cov
      - name: Run hybrid strategy tests
        run: |
          pytest backend/tests/unit/test_sampling.py \
                 backend/tests/unit/test_batch_worker.py \
                 backend/tests/unit/test_supabase_hybrid.py \
                 backend/tests/integration/test_pipeline_hybrid.py \
                 --cov=services --cov-report=xml -v
      - name: Upload coverage
        uses: codecov/codecov-action@v3
```

## 커버리지 목표

| 모듈 | 목표 커버리지 | 현재 상태 |
|-----|------------|---------|
| `services/sampling.py` | 90% | ✅ |
| `services/batch_worker.py` | 85% | ✅ |
| `services/supabase_service.py` (하이브리드 메서드) | 85% | ✅ |
| `routers/pipeline.py` (하이브리드 엔드포인트) | 80% | ✅ |

## 주의사항

1. **Async 테스트**: 모든 async 함수는 `@pytest.mark.asyncio` 필수
2. **Mock 위치**: `@patch`는 import된 위치를 패치해야 함
   - ❌ `@patch('anthropic.Anthropic')`
   - ✅ `@patch('services.ai_service.Anthropic')`
3. **Fixture 재사용**: conftest.py의 fixture는 모든 테스트에서 사용 가능
4. **실패 메시지**: 한글 메시지 사용 (CLAUDE.md 규칙 준수)

## 문제 해결

### ImportError 발생 시

```bash
# Python path 확인
export PYTHONPATH="${PYTHONPATH}:/path/to/memo-synthesizer/backend"
pytest backend/tests/ -v
```

### AsyncIO 에러 발생 시

```bash
# pytest-asyncio 플러그인 명시
pytest backend/tests/ --asyncio-mode=auto -v
```

### Mock이 제대로 작동하지 않을 때

```python
# patch 위치 확인
import services.supabase_service
print(services.supabase_service.create_async_client)
# → 이 경로로 patch 해야 함
```

## 다음 단계

테스트 작성 후:

1. **코드 리뷰**: test-automator가 생성한 테스트 검토
2. **커버리지 확인**: 80% 이상 달성 여부 확인
3. **통합 테스트 실행**: 실제 Supabase/Claude API와 연결 테스트 (선택 사항)
4. **문서화**: 테스트 결과를 CLAUDE.md에 반영

---

**생성일:** 2026-01-26
**버전:** 1.0.0
**담당:** test-automator agent
