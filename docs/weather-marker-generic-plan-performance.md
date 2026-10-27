# 마커 조회의 부분 인덱스와 generic plan

## 범위와 원인 근거

Weather99의 복합 fact 키 JOIN과 후보 한도 4를 보존한다. 내부 고정 metric/style/domain predicate만 SQL에 타입 안전하게 렌더링하고 위치 ID, 시각과 요청 한도는 bind parameter로 남긴다. 스키마·인덱스·UI·timeout은 변경하지 않는다.

운영 대표 위치 60개의 동일 시각 SQL을 PREPARE하여 force_custom_plan/force_generic_plan으로 EXPLAIN만 비교한 결과, observed 비용은 4269→56682, fallback은 3096→56669, alerts는 5481→29756으로 증가했고 generic plan에서 해당 부분 인덱스가 사라졌다. 각 prepared query의 generic/custom plan 실행 계수는 각각 1이었다. 이는 계획 선택을 강제한 비교이며 실제 API 연결이 generic plan을 선택했다는 증거나 실행 시간 개선 증거는 아니다.

## 검증과 한계

고정 predicate의 최종 SQL과 사용자 입력 bind 경계의 8회귀는 변경 전 4실패/4통과, 변경 후 8통과였다. 위치 문자열에 인용부호·SQL 주석·bind 형식 문자가 있어도 요청 값은 SQL 본문에 들어가지 않는다. 경보 시각/JSON key/후보·경보 한도는 계속 bind한다.

전용 UUID PostgreSQL DB에서 psycopg PREPARE로 3개 쿼리를 각각 force_custom_plan/force_generic_plan 실행해 결과 행 전체가 같은지 확인했다. 각 쿼리의 generic_plans/custom_plans 계수는 1/1이었다. 기존 Weather99 복합 키 회귀를 포함한 선택 검사는 16통과다. 이 작은 fixture의 결과 동등성은 운영 속도 개선 측정과 다르다.

전체 Python 검사 548통과(49warnings,601.87초), Ruff 통과, frontend type-check 및189회귀 통과를 확인했다. 운영 적용과 UI 504 해결은 별도 검증 전까지 미검증이다. 실제 API의 prepared statement 선택 여부, 운영 SQL 실행 시간, CPU·RSS 감소는 아직 입증하지 않았다. 고정 조건 이외의 입력을 SQL literal로 바꾸거나 전역 prepared statement를 끄지 않는다.


## 동일 대표 입력의 후속 계획 검증

root가 같은 실제 대표60·동일시각으로 새 고정 조건 쿼리3개에 custom/generic EXPLAIN을 각각 수행해6개 모두 성공했다. observed는 cost4268.97/1630.45와 IndexOnlyScan10개, fallback은3003.12/1901.63과 IndexOnlyScan10개를 두 모드 모두 유지했다. alerts는5512.68/5980.63과 IndexScan19/SeqScan1을 두 모드 모두 유지했다. 각 prepared query의 custom/generic 계수는1/1이었다. 기존 generic cost56682/56669/29756에서 발생하던 부분 인덱스 소실이 이 강제 계획 비교에서는 재현되지 않았다.

이 값은 EXPLAIN 추정 비용이며 실행 latency가 아니다. ANALYZE·실제 API pool의 generic 선택·배포 이후504 해결·CPU/RSS 개선은 이 결과로 증명하지 않는다. root 후속 원문 weather100-literal-custom-generic-plans-root-r1-20261016.private.json(SHA de17130dab60aaea2341082bbeffd919765bf2d1a44c22e3f499d58e6bb36c45)과 작성자 로컬 결과를 구분해 기록했다.


## 2026-10-28 — 실제 명시6종류의 특보 경계 후속

위 Weather99/무필터 계획은 당시 범위의 증거다. 실제 기본지도는6종류를 명시하며 현재/예보/
특보 projection 세 그룹을 읽는다. 새 후속은 `_kind_predicate`의 고정 특보5항목만 literal로
보존해 alert 부분 인덱스를 generic에서도 증명할 수 있게 한다. 요청값은 bind로 유지한다.
PG18 custom/generic rawplan4개는 alert_lookup와 정확한 fact 복합 키·partition pruning을
확인했지만, 이 후속을 포함하기 전 운영 API210의 cold60×3은19701ms200/30020ms504/21016ms200으로 실패했다.
운영 연결의 generic 선택·지연 기여는 미확정이고 전체 pointer/fact ORM·JSON 수화/입장대기는
별도다. browser 요청 예산 후속과 검증 게이트는 [새 정산](reports/2026-10-28-marker-request-budget.md)을 따른다.


### 전체 QA 정산과 운영 미검증

1d2 소스의 WSL전체1518 PASS와 test-only a707의 Docker전체1517 PASS/1 SKIP를 회수했다.
시험 한 파일을 제외한 나머지644파일은 바이트·mode가 같고, 차이는 중기예보 시험의 SDK 공개지연 계산 한 파일이다.
원 WSL27 QA실패·Docker2 시각 fixture실패와 보완 증거를 삭제하지 않았다. 소유 PG는 정상
exit0으로 종료했고 자원을 정리했다. 이는 운영 generic 선택·20초 지연 기여·25초 목표를 입증하지 않는다.
R6는21:33 UTC 정산 당시 준비 상태(입력False/None)이며 새 API+web 교체·실제 UI는 별도다.
