# Memo Synthesizer Backend

FastAPI 기반 백엔드 서비스로 Notion, Supabase, AI 서비스 통합을 통해 메모를 합성하고 관리합니다.

## 사전 요구사항

- Python 3.11 이상
- pip (Python 패키지 매니저)

## 설치 및 실행

### 1. 가상 환경 생성

```bash
cd backend
python -m venv venv
```

### 2. 가상 환경 활성화

**macOS/Linux:**
```bash
source venv/bin/activate
```

**Windows:**
```bash
venv\Scripts\activate
```

### 3. 의존성 설치

```bash
pip install -r requirements.txt
```

### 4. 환경 변수 설정

예제 환경 파일을 복사하고 API 키를 입력하세요:

```bash
cp .env.example .env
```

`.env` 파일을 편집하여 API 키를 추가하세요:
- `NOTION_API_KEY`: Notion 통합 토큰
- `NOTION_DATABASE_ID`: Notion 데이터베이스 ID
- `SUPABASE_URL`: Supabase 프로젝트 URL
- `SUPABASE_KEY`: Supabase API 키
- `OPENAI_API_KEY`: OpenAI API 키
- `ANTHROPIC_API_KEY`: Anthropic API 키

## 애플리케이션 실행

### 개발 서버

```bash
uvicorn main:app --reload
```

API는 다음 주소에서 이용 가능합니다:
- API: http://localhost:8000
- 대화형 문서 (Swagger UI): http://localhost:8000/docs
- 대안 문서 (ReDoc): http://localhost:8000/redoc

### 프로덕션 서버

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## API 엔드포인트

### Health Check

**GET** `/health`

API 상태를 확인합니다.

**응답:**
```json
{
  "status": "ok",
  "version": "0.1.0",
  "environment": "development"
}
```

### Pipeline (파이프라인)

#### RAW 레이어

**POST** `/pipeline/import-from-notion`

Notion에서 메모를 가져와 raw_notes 테이블에 저장합니다.

- **증분 import**: RPC 기반 변경 감지 (0.2초)
- **모드**: Database 또는 Parent Page
- **성능**: 첫 실행 ~3-5분 (724페이지), 이후 변경된 페이지만 가져오기

#### NORMALIZED 레이어

**POST** `/pipeline/extract-thoughts`

raw_notes에서 사고 단위를 추출하여 thought_units 테이블에 저장합니다.

**파라미터:**
- `auto_update_distance_table` (boolean, 기본 true): Distance Table 자동 갱신 여부

**동작:**
1. 사고 단위 추출 (Claude Sonnet 4.5)
2. 임베딩 생성 (OpenAI text-embedding-3-small)
3. Distance Table 자동 갱신 (신규 10개 이상일 때)

#### ZK 레이어

**POST** `/pipeline/collect-candidates`

약한 연결 페어 후보를 수집하여 pair_candidates 테이블에 저장합니다.

**파라미터:**
- `strategy` (string, 기본 "p10_p40"): 백분위수 범위 전략
- `use_distance_table` (boolean, 기본 true): Distance Table 사용 여부

**성능:**
- Distance Table 사용: 0.1초, 무제한 조회 ⭐ (권장)
- v4 Fallback: 60초+, 제한적

**POST** `/pipeline/sample-initial`

초기 100개 샘플을 AI로 평가합니다 (Claude Sonnet 4.5).

**POST** `/pipeline/score-candidates`

전체 후보를 배치로 AI 평가합니다 (백그라운드, ~5분/1000개).

#### Essay 레이어

**POST** `/pipeline/generate-essays`

선택된 페어로 Essay를 생성합니다 (Claude Sonnet 4.5).

**POST** `/pipeline/run-all`

전체 파이프라인을 실행합니다 (RAW → NORMALIZED → ZK → Essay).

### Distance Table (초고속 유사도 조회) ⭐

Distance Table은 모든 thought 페어의 유사도를 사전 계산하여 저장하는 기능입니다.

#### 성능 개선
- **조회 속도**: 0.1초 (vs 실시간 계산 60초+), **600배 개선**
- **조회 범위**: 무제한 (80% 범위 검증 완료, 100,000개 안전 상한선)
- **증분 갱신**: 신규 10개 추가 시 ~2초

#### 저장 공간
- **1,921개 기준**: 178MB (테이블 118MB + 인덱스 60MB)
- **Break-even**: 7회 조회부터 이득

**POST** `/pipeline/distance-table/build`

Distance Table을 초기 구축합니다 (한 번만 실행).

**파라미터:**
- `batch_size` (integer, 기본 50): 배치 크기 (25-100)

**응답:**
```json
{
  "status": "started",
  "message": "Distance table build started in background (~7min, batch_size=50)"
}
```

**성능**: 1,921개 기준 ~7분 (순차 배치 처리)

**GET** `/pipeline/distance-table/status`

Distance Table 통계를 조회합니다.

**응답:**
```json
{
  "total_pairs": 1843210,
  "min_similarity": 0.001,
  "max_similarity": 0.999,
  "avg_similarity": 0.123
}
```

**POST** `/pipeline/distance-table/update`

Distance Table을 증분 갱신합니다 (신규 thought 추가 시).

**파라미터:**
- `new_thought_ids` (array[integer], 선택): 신규 thought ID 목록 (기본 null = 자동 감지)

**응답:**
```json
{
  "success": true,
  "new_thought_count": 10,
  "new_pairs_inserted": 19210
}
```

**성능**: 신규 10개 × 1,921 기존 = ~2초

**자동 갱신**: `POST /pipeline/extract-thoughts?auto_update_distance_table=true` 실행 시 자동으로 갱신됩니다 (신규 10개 이상일 때).

### Essays (글감 관리)

**GET** `/essays`

Essay 목록을 조회합니다.

**GET** `/essays/{id}`

Essay 상세 정보를 조회합니다.

**GET** `/essays/recommended`

AI 추천 Essay 후보를 조회합니다 (평가 점수 기반 상위 후보).

## 프로젝트 구조

```
backend/
├── main.py                 # FastAPI 애플리케이션 진입점
├── config.py               # 설정 관리
├── requirements.txt        # Python 의존성
├── .env.example            # 환경 변수 템플릿
├── routers/                # API 라우트 핸들러
│   ├── __init__.py
│   └── health.py           # Health check 엔드포인트
├── services/               # 비즈니스 로직 레이어
│   └── __init__.py
├── models/                 # 데이터 모델
│   └── __init__.py
├── schemas/                # Pydantic 스키마
│   ├── __init__.py
│   └── health.py           # Health check 스키마
└── utils/                  # 헬퍼 함수
    └── __init__.py
```

## 개발

### 코드 스타일

이 프로젝트는 PEP 8 스타일 가이드를 따릅니다. 다음 도구 사용을 권장합니다:
- `black` - 코드 포맷팅
- `flake8` - 린팅
- `mypy` - 타입 체킹

### 테스팅

(테스팅 프레임워크 추가 예정)

## 외부 서비스

이 백엔드는 다음 서비스들과 통합됩니다:

- **Notion API**: 메모 저장 및 검색
- **Supabase**: 데이터베이스 및 인증
- **OpenAI**: GPT 기반 메모 합성
- **Anthropic**: Claude 기반 메모 합성

## 라이선스

MIT License - 루트 디렉토리의 LICENSE 파일을 참조하세요.
