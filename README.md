# Notion 아이디어 조합 서비스 (Memo Synthesizer)

노션 메모에서 사고 단위를 추출하고, 약한 연결을 찾아 글감을 생성하는 서비스입니다.

## 프로젝트 개요

**파이프라인:** RAW → NORMALIZED → ZK → Essay

1. **RAW**: Notion에서 메모 가져오기
2. **NORMALIZED**: 사고 단위 추출 + 임베딩 생성
3. **ZK**: 약한 연결 페어 발견 (상대적 백분위수 기반)
4. **Essay**: AI 평가 후 글감 생성

## 주요 기능

### 1. 증분 Import (RPC 기반 변경 감지)
- **성능**: 변경 감지 0.2초 (vs 전체 스캔 60초)
- **동작**: 신규/수정/삭제 페이지만 자동 감지하여 가져오기
- **확장성**: 100k 페이지까지 일정한 성능

### 2. Distance Table (초고속 유사도 조회)
- **성능**: 0.1초 (vs 실시간 계산 60초+), **600배 개선**
- **용량**: 1,921개 기준 178MB (테이블 118MB + 인덱스 60MB)
- **증분 갱신**: 신규 10개 추가 시 ~2초
- **조회 범위**: 무제한 (80% 범위 검증 완료, 100,000개 안전 상한선)
- **Break-even**: 7회 조회부터 이득

### 3. Hybrid Strategy C (상대적 백분위수 기반 후보 수집)
- **로직**: 전체 분포 대비 p10~p40 범위 (낮은 유사도 = 약한 연결)
- **평가**: Claude Sonnet 4.5 기반 논리적 확장 가능성 점수
- **추천**: AI 평가 점수 기반 상위 후보 제시

## 기술 스택

- **Backend**: FastAPI, Python 3.11+
- **Frontend**: Next.js 14 (App Router), TypeScript, Tailwind CSS
- **Database**: Supabase (PostgreSQL + pgvector)
- **LLM**: Claude 3.5 Sonnet (Anthropic), text-embedding-3-small (OpenAI)
- **External API**: Notion API

## 빠른 시작

### 사전 요구사항
- Python 3.11 이상
- Node.js 18 이상
- Supabase 계정
- Notion API 키
- OpenAI API 키
- Anthropic API 키

### 1. 백엔드 설정

```bash
cd backend

# 가상 환경 생성 및 활성화
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 의존성 설치
pip install -r requirements.txt

# 환경 변수 설정
cp .env.example .env
# .env 파일을 편집하여 API 키 입력

# 서버 실행
uvicorn main:app --reload
```

API는 http://localhost:8000 에서 실행됩니다.
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### 2. 프론트엔드 설정

```bash
cd frontend

# 의존성 설치
npm install

# 개발 서버 실행
npm run dev
```

프론트엔드는 http://localhost:3000 에서 실행됩니다.

### 3. 데이터베이스 설정

#### Supabase 데이터베이스 초기화

1. [Supabase Dashboard](https://supabase.com/dashboard)에 로그인
2. SQL Editor로 이동
3. 다음 마이그레이션 파일들을 순서대로 실행:

```bash
backend/docs/supabase_migrations/
├── 001_get_changed_pages.sql           # 증분 import RPC
├── 002_soft_delete_support.sql         # 삭제 감지
├── 003_add_delete_detection.sql        # 삭제 감지 확장
├── 004_create_hnsw_index.sql           # HNSW 인덱스
├── 005_v4_accuracy_first.sql           # 후보 검색 RPC (v4)
├── 006_create_pair_candidates.sql      # pair_candidates 테이블
├── 007_extend_thought_pairs.sql        # thought_pairs 확장
├── 008_create_similarity_distribution_cache.sql  # 분포 캐시
├── 009_v2_optimized_distribution.sql   # 분포 계산 RPC
├── 010_create_distance_table.sql       # Distance Table 생성 ⭐
├── 011_build_distance_table_rpc.sql    # Distance Table 초기 구축 RPC ⭐
└── 012_incremental_update_rpc.sql      # Distance Table 증분 갱신 RPC ⭐
```

⭐ 표시된 마이그레이션은 **Distance Table 핵심 기능**입니다.

자세한 내용은 [backend/docs/supabase_migrations/README.md](backend/docs/supabase_migrations/README.md)를 참고하세요.

## API 엔드포인트

### Pipeline (파이프라인)

```http
POST /pipeline/import-from-notion       # Step 1: RAW 수용
POST /pipeline/extract-thoughts         # Step 2: NORMALIZED 생성
POST /pipeline/collect-candidates       # Step 3: ZK 후보 수집 (Distance Table 사용)
POST /pipeline/sample-initial           # Step 4: 초기 샘플 AI 평가
POST /pipeline/score-candidates         # Step 5: 배치 AI 평가
POST /pipeline/generate-essays          # Step 6: Essay 생성
POST /pipeline/run-all                  # 전체 파이프라인 실행
```

### Distance Table (초고속 유사도 조회) ⭐

```http
POST /pipeline/distance-table/build     # Distance Table 초기 구축 (~7분)
GET  /pipeline/distance-table/status    # Distance Table 통계 조회
POST /pipeline/distance-table/update    # Distance Table 증분 갱신 (~2초/10개)
```

### Essays (글감 관리)

```http
GET  /essays                            # Essay 목록 조회
GET  /essays/{id}                       # Essay 상세 조회
GET  /essays/recommended                # AI 추천 Essay 후보
```

자세한 API 문서는 http://localhost:8000/docs 에서 확인하세요.

## 프로젝트 구조

```
memo-synthesizer/
├── backend/                    # FastAPI 백엔드
│   ├── main.py                 # FastAPI 앱 진입점
│   ├── config.py               # 환경 변수 설정
│   ├── routers/                # API 라우트
│   │   ├── pipeline.py         # 파이프라인 엔드포인트
│   │   └── essays.py           # Essay CRUD
│   ├── services/               # 비즈니스 로직
│   │   ├── supabase_service.py # DB 연동 (pgvector 포함)
│   │   ├── ai_service.py       # AI (Claude, OpenAI) 통합
│   │   ├── notion_service.py   # Notion API 연동
│   │   └── distance_table_service.py  # Distance Table 관리 ⭐
│   ├── schemas/                # Pydantic 스키마
│   └── docs/                   # 프로젝트 문서
│       ├── README.md           # 백엔드 상세 문서
│       └── supabase_migrations/ # SQL 마이그레이션
├── frontend/                   # Next.js 프론트엔드
│   ├── src/
│   │   ├── app/                # Next.js 14 App Router
│   │   ├── components/         # React 컴포넌트
│   │   └── lib/                # API 클라이언트
│   └── package.json
├── CLAUDE.md                   # Claude Code 프로젝트 가이드
└── README.md                   # 이 파일
```

## 성능 지표

### Import
- **증분 변경 감지**: 0.2초 (RPC 기반, 100k 페이지 확장 가능)
- **첫 실행**: 724페이지 ~3-5분 (Notion API rate limit)
- **이후 실행**: 변경된 페이지만 가져오기

### 후보 수집
- **Distance Table (권장)**: 0.1초, 무제한 조회 ⭐
- **v4 Fallback**: 60초+, 제한적

### Distance Table
- **초기 구축**: 1,921개 기준 ~7분 (순차 배치 처리, 한 번만 실행)
- **증분 갱신**: 신규 10개 ~2초 (자동 실행)
- **저장 공간**: 1,921개 기준 178MB
- **Break-even**: 7회 조회부터 이득

### AI 평가
- **초기 샘플 (100개)**: ~30초
- **배치 평가 (1000개)**: ~5분 (백그라운드)

## 주요 컨셉

### 약한 연결 (Weak Ties)
- **목표**: 서로 다른 아이디어 조합을 통한 창의적 통찰
- **유사도 범위**: 상대적 백분위수 p10~p40 (낮은 유사도)
- **이유**: 너무 유사하면 새로운 통찰이 없고, 너무 다르면 연결이 어려움

### 상대적 백분위수 (Relative Percentile)
- **방법**: 전체 유사도 분포를 100등분하여 p10~p40 범위 선택
- **장점**: 데이터 규모와 무관하게 일정한 비율 유지
- **예시**: 1,921개 기준 p10=0.077, p40=0.138

### Distance Table
- **개념**: 모든 thought 페어의 유사도를 사전 계산하여 저장
- **장점**:
  - 초고속 조회 (0.1초, 600배 개선)
  - 무제한 범위 조회 가능
  - 증분 갱신 가능 (신규 thought 추가 시)
- **Trade-off**:
  - 저장 공간: N×(N-1)/2 페어 (1,921개 → 178MB)
  - Break-even: 7회 조회부터 이득
  - 초기 구축 시간: ~7분 (한 번만 실행)

## 문서

- [backend/README.md](backend/README.md) - 백엔드 상세 문서
- [backend/docs/README.md](backend/docs/README.md) - 프로젝트 문서 인덱스
- [backend/docs/supabase_migrations/README.md](backend/docs/supabase_migrations/README.md) - SQL 마이그레이션 가이드
- [CLAUDE.md](CLAUDE.md) - Claude Code 프로젝트 가이드 (에이전트용)

## 라이선스

MIT License - LICENSE 파일 참조
