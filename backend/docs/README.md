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

## 프로젝트 구조

```
backend/
├── main.py                 # FastAPI 애플리케이션 진입점
├── config.py               # 설정 관리
├── requirements.txt        # Python 의존성
├── .env.example            # 환경 변수 템플릿
├── docs/                   # 프로젝트 문서 (핵심 참조 자료)
│   ├── README.md           # 상세 문서 (이 파일)
│   ├── VERIFICATION_SUMMARY.md  # DB 검증 요약
│   ├── supabase_setup.sql       # DB 초기화 SQL
│   ├── supabase_verification_queries.sql  # DB 검증 쿼리
│   └── setup_database.py        # DB 초기화 스크립트
├── temp/                   # 임시 파일 (개발 중 생성, .gitignore 적용)
│   ├── experiments/        # 모델 비교 실험 결과
│   └── verification/       # 검증 및 분석 스크립트
├── backups/                # 백업 데이터
│   └── README.md
├── routers/                # API 라우트 핸들러
│   ├── __init__.py
│   ├── health.py           # Health check 엔드포인트
│   ├── pipeline.py         # 파이프라인 엔드포인트
│   └── ...
├── services/               # 비즈니스 로직 레이어
│   ├── __init__.py
│   ├── ai_service.py       # AI (Claude, OpenAI) 통합
│   ├── supabase_service.py # Supabase DB 연동
│   ├── notion_service.py   # Notion API 연동
│   ├── distance_table_service.py  # Distance Table 관리 (NEW)
│   └── ...
├── schemas/                # Pydantic 스키마
│   ├── __init__.py
│   ├── raw.py              # RAW 레이어 스키마
│   ├── normalized.py       # NORMALIZED 레이어 스키마
│   ├── zk.py               # ZK 레이어 스키마
│   ├── essay.py            # Essay 스키마
│   └── ...
└── utils/                  # 헬퍼 함수
    └── __init__.py
```

### 폴더별 설명

#### docs/ - 프로젝트 문서
프로젝트 운영에 필요한 핵심 문서들을 보관합니다.
- `README.md`: 상세 프로젝트 문서 (이 파일)
- `VERIFICATION_SUMMARY.md`: Step별 검증 결과 요약
- `supabase_setup.sql`: Supabase 데이터베이스 초기화 SQL
- `supabase_verification_queries.sql`: DB 상태 확인 쿼리 모음
- `setup_database.py`: Python 기반 DB 초기화 스크립트
- `supabase_migrations/`: SQL 마이그레이션 파일 (RPC 함수, 스키마 변경)
  - `001-009`: 증분 import, HNSW 인덱스, 후보 검색, 분포 계산 등
  - `010-012`: **Distance Table** (초고속 유사도 조회, NEW) ⭐

#### temp/ - 임시 파일
개발 과정에서 생성된 임시 파일들을 보관합니다. (`.gitignore`에 포함)
- `experiments/`: 모델 비교, 백업 등 실험 관련 파일
- `verification/`: DB 검증, 상태 확인 등 분석 스크립트

#### backups/ - 백업 데이터
중요 데이터의 백업을 저장합니다.

## 개발

### 코드 스타일

이 프로젝트는 PEP 8 스타일 가이드를 따릅니다. 다음 도구 사용을 권장합니다:
- `black` - 코드 포맷팅
- `flake8` - 린팅
- `mypy` - 타입 체킹

### 테스팅

(테스팅 프레임워크 추가 예정)

## 데이터베이스 초기화

### Supabase 데이터베이스 설정

프로젝트를 처음 시작할 때 Supabase 데이터베이스를 초기화해야 합니다.

#### 방법 1: Supabase SQL Editor 사용 (권장)

1. [Supabase Dashboard](https://supabase.com/dashboard)에 로그인
2. 프로젝트 선택 → SQL Editor로 이동
3. `docs/supabase_setup.sql` 파일의 내용을 복사
4. SQL Editor에 붙여넣고 실행

#### 방법 2: Python 스크립트 사용

```bash
python docs/setup_database.py
```

**참고**: Python 스크립트는 안내만 제공하며, 실제 SQL은 Supabase Dashboard에서 실행해야 합니다.

### 데이터베이스 검증

DB 초기화 후 다음 파일로 상태를 확인할 수 있습니다:
- `docs/supabase_verification_queries.sql`: 테이블, 인덱스, 데이터 확인 쿼리
- `docs/VERIFICATION_SUMMARY.md`: Step별 검증 체크리스트

## 주요 기능

### 1. 증분 Import (RPC 기반 변경 감지)
- **성능**: 변경 감지 0.2초 (vs 전체 스캔 60초)
- **동작**: 신규/수정/삭제 페이지만 자동 감지하여 가져오기
- **구현**: RPC 함수 `get_changed_pages` (001_get_changed_pages.sql)

### 2. Distance Table (초고속 유사도 조회) ⭐
- **성능**: 0.1초 (vs 실시간 계산 60초+), **600배 개선**
- **용량**: 1,921개 기준 178MB (테이블 118MB + 인덱스 60MB)
- **증분 갱신**: 신규 10개 추가 시 ~2초
- **조회 범위**: 무제한 (80% 범위 검증 완료, 100,000개 안전 상한선)
- **Break-even**: 7회 조회부터 이득
- **구현**:
  - 테이블: `thought_pair_distances` (010_create_distance_table.sql)
  - RPC: `build_distance_table_batch` (011_build_distance_table_rpc.sql)
  - RPC: `update_distance_table_incremental` (012_incremental_update_rpc.sql)
  - Service: `backend/services/distance_table_service.py`

### 3. Hybrid Strategy C (상대적 백분위수 기반 후보 수집)
- **로직**: 전체 분포 대비 p10~p40 범위 (낮은 유사도 = 약한 연결)
- **평가**: Claude Sonnet 4.5 기반 논리적 확장 가능성 점수
- **추천**: AI 평가 점수 기반 상위 후보 제시

## 외부 서비스

이 백엔드는 다음 서비스들과 통합됩니다:

- **Notion API**: RAW 메모 가져오기
- **Supabase**: PostgreSQL + pgvector 데이터베이스
- **OpenAI**: text-embedding-3-small 임베딩 생성
- **Anthropic**: Claude Sonnet 4.5 사고 단위 추출 및 글감 생성

## 라이선스

MIT License - 루트 디렉토리의 LICENSE 파일을 참조하세요.
