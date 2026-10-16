# 마커 조회의 부분 인덱스와 generic plan

## 범위와 원인 근거

Weather99의 복합 fact 키 JOIN과 후보 한도 4를 보존한다. 내부 고정 metric/style/domain predicate만 SQL에 타입 안전하게 렌더링하고 위치 ID, 시각과 요청 한도는 bind parameter로 남긴다. 스키마·인덱스·UI·timeout은 변경하지 않는다.

운영 대표 위치 60개의 동일 시각 SQL을 PREPARE하여 force_custom_plan/force_generic_plan으로 EXPLAIN만 비교한 결과, observed 비용은 4269→56682, fallback은 3096→56669, alerts는 5481→29756으로 증가했고 generic plan에서 해당 부분 인덱스가 사라졌다. 각 prepared query의 generic/custom plan 실행 계수는 각각 1이었다. 이는 계획 선택을 강제한 비교이며 실제 API 연결이 generic plan을 선택했다는 증거나 실행 시간 개선 증거는 아니다.

## 검증과 한계

고정 predicate의 최종 SQL과 사용자 입력 bind 경계의 8회귀는 변경 전 4실패/4통과, 변경 후 8통과였다. 위치 문자열에 인용부호·SQL 주석·bind 형식 문자가 있어도 요청 값은 SQL 본문에 들어가지 않는다. 경보 시각/JSON key/후보·경보 한도는 계속 bind한다.

전용 UUID PostgreSQL DB에서 psycopg PREPARE로 3개 쿼리를 각각 force_custom_plan/force_generic_plan 실행해 결과 행 전체가 같은지 확인했다. 각 쿼리의 generic_plans/custom_plans 계수는 1/1이었다. 기존 Weather99 복합 키 회귀를 포함한 선택 검사는 16통과다. 이 작은 fixture의 결과 동등성은 운영 속도 개선 측정과 다르다.

기존 저장소 전체 Python 검사와 frontend 검사는 별도로 수행한다. 운영 적용과 UI 504 해결은 별도 검증 전까지 미검증이다. 실제 API의 prepared statement 선택 여부, 운영 SQL 실행 시간, CPU·RSS 감소는 아직 입증하지 않았다. 고정 조건 이외의 입력을 SQL literal로 바꾸거나 전역 prepared statement를 끄지 않는다.
