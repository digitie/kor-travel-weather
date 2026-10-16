# 마커 조회의 부분 인덱스와 generic plan

## 범위와 원인 근거

Weather99의 복합 fact 키 JOIN과 후보 한도 4를 보존한다. 내부 고정 metric/style/domain predicate만 SQL에 타입 안전하게 렌더링하고 위치 ID, 시각과 요청 한도는 bind parameter로 남긴다. 스키마·인덱스·UI·timeout은 변경하지 않는다.

운영 대표 위치 60개의 동일 시각 SQL을 PREPARE하여 force_custom_plan/force_generic_plan으로 EXPLAIN만 비교한 결과, observed 비용은 4269→56682, fallback은 3096→56669, alerts는 5481→29756으로 증가했고 generic plan에서 해당 부분 인덱스가 사라졌다. 각 prepared query의 generic/custom plan 실행 계수는 각각 1이었다. 이는 계획 선택을 강제한 비교이며 실제 API 연결이 generic plan을 선택했다는 증거나 실행 시간 개선 증거는 아니다.

## 검증 계획

고정 predicate의 최종 SQL과 사용자 입력 bind 경계를 검사한다. 같은 입력의 결과와 복합 키·경보 revision 의미를 PostgreSQL 회귀로 확인한다. 기존 저장소 전체 Python 검사와 frontend 검사를 수행한다. 운영 적용과 UI 504 해결은 별도 검증 전까지 미검증이다.
