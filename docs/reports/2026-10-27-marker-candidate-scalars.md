# 무필터 API 마커의 후보 fact 재JOIN 제거

## 2026-10-27 후속 정정

아래 "기본 지도" 경로 판단은 당시 분석 오류다. 실제 web5098은 `kindFilter`가
없어도 여섯 종류를 명시하며 API는 이를 `None`으로 바꾸지 않는다. PR180은
`kinds=None` API 경로를 개선했으며 기본 지도6종류 경로를 검증한 것이 아니다.
아래 EXPLAIN·scalar 비교와 이후 정확593의 WSL1443·Docker1442/skip1 결과는
무필터 경로 검증으로 보존하며 실제 UI 성능으로 확대하지 않는다.
[실제 경로·취소 재현과 최소 수정](2026-10-27-marker-disconnect-admission.md)을 따른다.

## 확인한 병목 후보

당시에는 "기본 지도는 `kindFilter=undefined`이므로
`marker_values_many(kinds=None, providers=None)` 경로를 사용한다"고 판단했다.
이 인과는 위와 같이 정정한다. 아래 분석 대상은 무필터 API 경로다.0034의
`weather_current_values` 부분 인덱스 검증을 이 fact 경로의 개선 증거로 확대하지 않는다.
현재 main176688의 관련 세 메서드는 운영a9와 동일하며 그 사이 repository 변경은
`purge_expired_short_forecast`에만 있다.174/175의 정리·export 변경을 보존한 main에서 작업했다.

저장된 실제60ID 한 batch와06:48:08.291UTC 요청 시작 시각으로 운영 설치 모듈의 정확
`marker_values_many` 원문을 검산했다. 실행 없는 EXPLAIN은 관측 후보 SELECT 한 개만
명시 READ ONLY·statement3초·lock500ms로 수행했다. HTTP/provider/job·DB 쓰기·ANALYZE는 없다.
첫 두 진단은 원문 해시 비교 시 마지막 개행 처리의 assertion에서 중단돼 SQL 실행 전 실패했고
원본을 보존했다. 개행 경계만 맞춘 후 정확 함수 hash는
`c4cb07e94a22be4f74ec3d7c89a1c4308f79917211783fbca5f545d199101bf8`로 일치했다.

관측 후보는 location×6metric마다 LATERAL LIMIT4로 key2열을 선택한 다음 같은
`weather_values` fact에 `(value_id, known_at)`으로 재JOIN해 비교용 scalar5열을 얻는다.
계획에는16개 날짜/default partition의 Index Only Scan→후보 LIMIT4→Memoize/PK Append가
나타났다. 이 노드 수는 실제 읽은 partition/loops 수가 아니다. 실행하지 않았으므로 runtime
pruning·실제 I/O·각 SQL의 latency 기여도는 확인하지 않았다. 계획 최상단 추정2145600행도
실제 반환 행 수가 아니다.

기존 비교 후보는 이미 `location_id, metric_key, known_at, source_record_key, value_id`의
scalar5열만 선택한다. 이 단계에서 raw JSON/전체ORM을 읽었다고 주장하지 않는다.
60×6×top4의 후보 상한은1440이고 Python이 위치/metric당 승자를 하나 골라 최종 fact를
수화하므로 승자 상한은360이다. fallback은 관측에서 누락된 pair에만 적용된다.
최종 승자·특보의 전체 원문 수화와 응답은 유지한다.

## 최소 변경

관측/누락 pair fallback의 기존 LATERAL에서 동일한 scalar5열을 직접 반환한다. 후보의
같은 fact 재JOIN만 제거한다. 정렬·top4·Python tie 비교·관측 우선·시각 창·fallback pair·
특보·known/unknown·최종 winner와 raw lineage·공개DTO·writer는 바꾸지 않는다.
새 인덱스·migration·provider 동작·timeout/병렬도/재시도 설정은 추가하지 않는다.

이는 실제 계획에서 확인한 중복 관계 접근 한 개를 제거하는 변경이다. 운영의 첫60×3
19.506/29.884/28.324초 전체를 이 JOIN 하나가 설명하거나25초 목표가 달성됐다고
주장하지 않는다. 운영 교체와 지연 실패는 별도 [배포 정산 PR179](https://github.com/digitie/kor-travel-weather/pull/179)를 따른다.

## 회귀와 현재 게이트

- 변경 전 구조 회귀는 관측/fallback의 불필요한 `marker_fact_keys` 재JOIN으로2개 모두
  실패했다. 첫 시도는 전용 PG가 종료돼 connection refused였으며 이 환경 오류를 RED로 세지 않는다.
- 새 전용 PostgreSQL16 fixture에서 기존 top4+복합 키 재JOIN 쿼리와 변경된 관측/fallback
  후보의 전체 scalar row를 비교한다. 같은 known_at/source tie·다른 partition의 같은UUID·
  같은metric의 미래forecast·누락metric fallback·특보 제외/최종특보 보존·빈 위치를 포함한다.
- 기존 winner hydration·Python tie·retention으로 사라진 승자·복합 키 partition·generic/custom
  계약과 합친27개가26.22초에 통과했다. 새 fixture의 첫 source identity 충돌은 fixture가
  provider/dataset별 독립 source를 기록하도록 고쳤고 제품에서 우회하지 않았다.
- 변경한 Python 파일 Ruff가 통과했다. 전체 WSL·Docker2차·정확HEAD CI·독립 두 리뷰는
  아직 진행 전/진행 중이다. 그 결과를 이 집중 검증으로 대체하지 않는다.
- 운영 새 이미지 빌드·교체·추가 UI/직접 provider/job 호출은 하지 않았다.

원본은 `/var/tmp/weather173-marker-runtime-plan-20261027-48ca6068.json`과
`/var/tmp/weather173-marker-scalar-qa-20261027/`에 보존했다. 본문 지연 실패와 취소 원장은
위 배포 문서의 원본 경로를 유지한다. 후속 실제 수용은 코드 게이트 통과 후 장기 예보 run을
pause/G 전에 자연 종료시키는 별도 짧은 배포 절차를 검토한 뒤 진행한다.
