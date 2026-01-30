# Distance Table 재구축 검증 리포트

**검증 일시:** 2026-01-29
**검증자:** Claude Code (test-automator)
**테스트 스크립트:** `scripts/verify_distance_table_rebuild.py`

---

## 요약

Distance Table v2 재구축이 **37.46%에서 중단**된 것으로 확인되었습니다.

- **thought_units 개수:** 1,909개
- **예상 페어 수:** 1,821,186개 (n×(n-1)/2)
- **현재 페어 수:** 682,271개
- **완료율:** 37.46%
- **남은 페어:** 1,138,915개

---

## 검증 결과 상세

### 1. thought_units 테이블 레코드 수 확인

```
✅ thought_units 개수: 1,909개
✅ 예상 페어 수: 1,821,186개 (n×(n-1)/2)
```

- **통과:** thought_units 테이블에 1,909개의 레코드 존재 (embedding NOT NULL)
- **계산:** 1,909 × 1,908 / 2 = 1,821,186 페어 예상

### 2. 현재 Distance Table 페어 수 확인

```
✅ 현재 Distance Table 페어 수: 682,271개
✅ 예상 페어 수: 1,821,186개
✅ 완료율: 37.46%
✅ 남은 페어: 1,138,915개

유사도 통계:
  - 최소: 0.0015
  - 최대: 0.0678
  - 평균: 0.0514
```

- **상태:** 재구축이 37.46%에서 중단됨
- **문제:** 7분 경과 후에도 진행 없음 (사용자 리포트)
- **유사도 범위:** 0.0015 ~ 0.0678 (정상 범위)

### 3. thought_a_id, thought_b_id 범위 확인

```
✅ thought_a_id 범위: 1 ~ 1550
✅ thought_b_id 범위: 251 ~ 1909
✅ thought_units 최대 ID: 1909

⚠️ 누락된 thought_a_id 범위: 1551 ~ 1909
   (재구축이 완료되지 않았을 가능성)
```

- **발견:** thought_a_id가 1~1550까지만 처리됨
- **누락:** 1551~1909 범위의 thought_a_id가 누락됨 (359개)
- **분석:** 배치 처리가 thought_a_id=1550 근처에서 중단됨

### 4. 샘플 페어 존재 여부 확인

```
  ❌ 페어 (1, 2): 누락
  ❌ 페어 (1, 3): 누락
  ❌ 페어 (50, 51): 누락
  ❌ 페어 (100, 101): 누락
  ❌ 페어 (500, 501): 누락
  ❌ 페어 (1000, 1001): 누락
  ✅ 페어 (1500, 1501): 존재 (similarity=0.3305)
  ❌ 페어 (1900, 1901): 누락

✅ 샘플 페어 조회 결과: 1/8 페어 발견
⚠️ 누락된 페어: [(1, 2), (1, 3), (50, 51), (100, 101), (500, 501), (1000, 1001), (1900, 1901)]
```

- **발견:** 8개 샘플 페어 중 1개만 존재
- **유일 존재:** (1500, 1501) - thought_a_id=1500은 처리 범위 내
- **누락:** thought_a_id < 1550인 페어들도 대부분 누락 (주목!)
  - (1, 2), (1, 3), (50, 51) 등은 thought_a_id가 작은데도 누락됨
  - **가능성:** 배치 처리가 thought_a_id 순서가 아닌 다른 순서로 진행됨

---

## 문제 분석

### 근본 원인

1. **배치 처리 중단:**
   - 재구축 프로세스가 37.46%에서 멈춤
   - 7분 경과 후에도 진행 없음 (사용자 보고)

2. **가능한 원인:**
   - **타임아웃:** RPC 함수가 60초 타임아웃에 걸림
   - **메모리 부족:** Supabase Free tier 메모리 제한
   - **연결 끊김:** 네트워크 또는 DB 연결 문제
   - **프로세스 중단:** Python 프로세스가 강제 종료됨

3. **예상치 못한 발견:**
   - thought_a_id < 100인 작은 ID 페어들도 누락됨
   - 배치 처리가 순차적이지 않을 수 있음
   - RPC 함수가 offset 순서가 아닌 다른 방식으로 처리했을 가능성

### 영향

- **collect-candidates 성능:** Distance Table 조회가 불완전함
- **Essay 생성:** 일부 thought 조합을 찾지 못함
- **재구축 시간:** 남은 62.54%를 재실행해야 함

---

## 권장 조치

### 즉시 조치 (High Priority)

1. **로그 확인:**
   ```bash
   # Backend 로그 확인
   tail -f backend/logs/app.log

   # Supabase 대시보드에서 RPC 실행 로그 확인
   # https://supabase.com/dashboard/project/YOUR_PROJECT_ID/logs
   ```

2. **재구축 재실행:**
   ```bash
   POST /pipeline/distance-table/build?batch_size=50
   ```
   - 기존 데이터는 TRUNCATE되므로 처음부터 재시작
   - batch_size=50은 검증된 값 (권장)

3. **모니터링:**
   ```bash
   # 1분마다 진행 상황 확인
   watch -n 60 "curl -X GET http://localhost:8000/pipeline/distance-table/status"
   ```

### 중기 조치 (Medium Priority)

1. **배치 처리 개선:**
   - RPC 함수에 체크포인트 추가 (부분 실패 시 재시작 가능)
   - 진행 상황을 별도 테이블에 저장
   - 타임아웃 발생 시 자동 재시도

2. **배치 크기 조정:**
   - 현재: batch_size=50 (안전)
   - 대안: batch_size=25 (더 안전, 느림)
   - 테스트: batch_size=100 (위험, 타임아웃 가능)

3. **증분 재구축 지원:**
   - 처음부터 재시작이 아닌, 중단 지점부터 재개
   - thought_a_id 범위를 지정하여 부분 재구축

### 장기 조치 (Low Priority)

1. **Supabase Tier 업그레이드:**
   - Free tier: 60초 타임아웃, 15개 연결 제한
   - Pro tier: 120초 타임아웃, 더 많은 메모리

2. **대안 아키텍처:**
   - 백그라운드 작업자 (Celery, Redis)
   - 배치 작업 큐 (Bull, RabbitMQ)
   - Scheduled Job (Supabase Edge Functions + Cron)

---

## 테스트 커버리지

### 작성된 테스트

1. **Mock 테스트:** `tests/integration/test_distance_table_rebuild_verification.py`
   - 재구축 검증 시나리오 (37.5% 중단 상황 재현)
   - ID 범위 검증
   - 샘플 페어 조회

2. **실제 DB 테스트:** `tests/integration/test_distance_table_rebuild_live.py`
   - 실시간 DB 연결 (환경변수 필요)
   - 무결성 검사 (UNIQUE, CHECK constraint)
   - similarity 범위 검증

3. **검증 스크립트:** `scripts/verify_distance_table_rebuild.py`
   - 종합 리포트 생성
   - 실시간 진행 상황 모니터링
   - 권장 조치 제시

### 실행 방법

```bash
# Mock 테스트 (환경변수 불필요)
pytest tests/integration/test_distance_table_rebuild_verification.py -v

# 실제 DB 테스트 (환경변수 필요)
pytest tests/integration/test_distance_table_rebuild_live.py -v -s

# 검증 스크립트 (권장)
python scripts/verify_distance_table_rebuild.py
```

---

## 다음 단계

1. **재구축 재실행 및 모니터링**
2. **완료 후 검증 스크립트 재실행**
3. **완료율 100% 확인**
4. **샘플 페어 8개 모두 존재하는지 확인**
5. **collect-candidates 성능 테스트**

---

## 검증 체크리스트

- [x] thought_units 개수 확인 (1,909개)
- [x] 예상 페어 수 계산 (1,821,186개)
- [x] 현재 페어 수 확인 (682,271개, 37.46%)
- [x] ID 범위 확인 (thought_a_id: 1~1550, 누락: 1551~1909)
- [x] 샘플 페어 조회 (1/8 발견, 7/8 누락)
- [ ] 재구축 재실행 (권장 조치)
- [ ] 완료율 100% 달성 확인
- [ ] 샘플 페어 8/8 존재 확인
- [ ] 무결성 검사 (UNIQUE, CHECK, NULL)
- [ ] 성능 테스트 (조회 0.1초 이내)

---

**검증 완료:** ✅
**재구축 필요:** ⚠️ (37.46% → 100% 목표)
