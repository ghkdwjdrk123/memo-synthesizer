-- ============================================================
-- 021: Distance Table 정리 (폐기)
-- ============================================================
-- 목적: 샘플링 기반 마이닝으로 전환 후 Distance Table 관련 객체 삭제
-- 실행: Supabase SQL Editor에서 수동 실행
-- ============================================================

-- 1. RPC 함수 삭제
DROP FUNCTION IF EXISTS build_distance_table_batch CASCADE;
DROP FUNCTION IF EXISTS update_distance_table_incremental CASCADE;
DROP FUNCTION IF EXISTS fill_missing_pairs_for_range CASCADE;
DROP FUNCTION IF EXISTS calculate_distribution_from_distance_table CASCADE;

-- 2. 테이블 삭제
DROP TABLE IF EXISTS thought_pair_distances CASCADE;

-- 3. 확인
SELECT 'Distance Table cleanup completed' AS status;

-- ============================================================
-- 참고: 새로운 아키텍처
-- ============================================================
--
-- 기존 Distance Table 방식 (폐기됨):
--   - thought_pair_distances: 전쌍 유사도 저장 (N×(N-1)/2)
--   - build_distance_table_batch(): 초기 구축 (~7분)
--   - update_distance_table_incremental(): 증분 갱신
--   - calculate_distribution_from_distance_table(): 분포 계산
--
-- 새로운 샘플링 기반 방식:
--   - similarity_samples: 랜덤 샘플 저장 (~10만개)
--   - pair_candidates: 마이닝된 후보 페어
--   - pair_mining_progress: 마이닝 진행 상태
--   - build_distribution_sketch(): 샘플 수집
--   - calculate_distribution_from_sketch(): 근사 분포 계산
--   - mine_candidate_pairs(): 샘플링 기반 후보 생성
--
-- 장점:
--   - 초기 구축: 7분 → 3초 (140배 빠름)
--   - 저장 공간: 178MB → ~5MB (35배 절감)
--   - 확장성: O(N²) → O(N×k)
-- ============================================================
