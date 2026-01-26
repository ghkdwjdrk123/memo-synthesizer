# Claude Code Agent 사용 규칙

프로젝트 진행 시 반드시 지켜야 할 에이전트 활용 규칙입니다.

## 🤖 에이전트 의무 사용 규칙

### 1. 코드 변경 후 → test-automator (필수)
```
상황: 어떤 코드를 변경했을 때
행동: IMMEDIATELY use test-automator
이유: 모든 코드 변경은 테스트가 필요
```

### 2. 구현 완료 후 → code-reviewer (필수)
```
상황: 기능 구현이 완료되었을 때
행동: use code-reviewer BEFORE marking complete
이유: 코드 품질, 보안, 성능 검증
```

### 3. 에러 발생 시 → debugger (권장)
```
상황: 에러, 예상치 못한 동작, 테스트 실패
행동: use debugger
이유: 체계적 디버깅
```

### 4. LLM 작업 시 → prompt-engineer (필수)
```
상황: Claude/OpenAI API, 프롬프트 설계, JSON 출력
행동: MUST use prompt-engineer
이유: LLM 전문 에이전트
```

### 5. DB 작업 시 → supabase-specialist (필수)
```
상황: Supabase, pgvector, PostgreSQL 쿼리
행동: MUST use supabase-specialist
이유: DB 및 벡터 검색 전문
```

### 6. Frontend 작업 시 → nextjs-developer (필수)
```
상황: Next.js 14, React 컴포넌트, .tsx 파일
행동: MUST use nextjs-developer
이유: Next.js App Router 전문
```

### 7. Backend 작업 시 → fastapi-architect (필수)
```
상황: FastAPI, async 패턴, 서비스 레이어
행동: MUST use fastapi-architect
이유: FastAPI 패턴 전문
```

### 8. 코드베이스 탐색 시 → Explore (권장)
```
상황: 복잡한 파일 검색, 코드 구조 파악
행동: use Explore agent
이유: 수동 Glob/Grep보다 효율적
```

### 9. 복잡한 계획 필요 시 → Plan (권장)
```
상황: 복잡한 구현 전 설계 필요
행동: use Plan agent
이유: 상세한 단계별 계획 생성
```

### 10. 파일 정리 시 → file-organizer (선택)
```
상황: 임시 파일 정리, 프로젝트 구조 정리
행동: use file-organizer
이유: 사용자 확인 후 안전 삭제
```

## 📝 문서 작성 규칙

### 1. 새 .md 파일 생성 전 확인
```
행동: 기존 .md 파일 검색 → 유사 주제 있으면 추가, 없으면 생성
이유: 중복 문서 방지, 정보 통합
```

### 2. 외부 질의 프롬프트
```
폴더: backend/docs/queries/
파일명: YYYY-MM-DD_주제.md
용도: Perplexity, ChatGPT 등 외부 AI 질의
```

### 3. 내부 분석 문서
```
폴더: backend/docs/analysis/
파일명: 주제_issue.md 또는 주제_analysis.md
용도: 프로젝트 내부 문제 분석
```

### 4. README 필수
```
조건: 새 폴더 생성 시
행동: 항상 README.md 작성
내용: 폴더 목적, 파일 목록, 사용 방법
```

## 🗂️ 파일 조직 규칙

### 폴더 구조
```
backend/docs/
├── queries/              # 외부 질의 프롬프트
│   ├── README.md
│   └── YYYY-MM-DD_주제.md
├── analysis/             # 내부 분석 문서
│   ├── README.md
│   └── 주제_issue.md
├── supabase_migrations/  # DB 마이그레이션
│   ├── NNN_이름.sql      # 메인 마이그레이션
│   └── NNN-1_이름.sql    # 서브 마이그레이션
└── *.md                  # 기타 문서 (알고리즘, 가이드 등)
```

### 파일명 규칙
- **SQL 파일**: `NNN_snake_case.sql` (파생: `NNN-1_snake_case.sql`)
- **질의 프롬프트**: `YYYY-MM-DD_주제.md`
- **분석 문서**: `주제_issue.md` 또는 `주제_analysis.md`
- **일반 문서**: `snake_case.md`

## ⚠️ 중요 원칙

1. **에이전트 우선**: 수동 작업보다 에이전트 사용 우선
2. **병렬 실행**: 가능하면 여러 에이전트 동시 실행
3. **문서 통합**: 새 파일 생성 전 기존 파일 확인
4. **README 필수**: 모든 새 폴더에 README.md
5. **한국어 우선**: 모든 출력과 문서는 한국어

## 📋 체크리스트

### 코드 작성 후
- [ ] test-automator 실행
- [ ] code-reviewer 실행
- [ ] 관련 문서 업데이트

### 문서 작성 시
- [ ] 기존 유사 문서 검색
- [ ] 적절한 폴더에 배치
- [ ] README 업데이트

### SQL 마이그레이션 시
- [ ] 넘버링 확인 (NNN, NNN-1)
- [ ] Parent 명시 (서브 마이그레이션)
- [ ] 롤백 쿼리 포함

---

**버전**: 1.0.0
**최종 업데이트**: 2026-01-26
