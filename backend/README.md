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
