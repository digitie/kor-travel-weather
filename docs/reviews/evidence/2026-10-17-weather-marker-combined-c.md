# Weather 통합 marker 개선 제3자 독립 FULL 리뷰 C r1

- 요청자: root. 시작 식별자: weather-finish-performance-review-c-r1-20261017.
- 원 요청 범위: 통합 HEAD58230797b1b3c932537b28fc8ca5139e8075c49e, fixed base0acb11fc94d1427f7e920d3feed8a69fac515c21 대비 A270(frontend2파일)+D049(repository/tests5파일)의 독립 적대적 FULL 검토.
- 판정: **두 제품 모두 SOURCE_PASS, OPEN P0/P1/P2=0**.
- C는 이 두 delta의 작성자가 아니다. 앞선 controller 작성자 검증을 이 리뷰 수에 포함하지 않았다. A/D peer 리뷰 원문과 작성자 실행 결과를 읽거나 합산하지 않았다.
- 검토/모형/보고서만 작성했다. 통합 WT 편집0, DB/SDK/브라우저/HTTP/SSH/운영0, 성능 측정0. root의 진행 중 fullpytest를 건드리거나 중복 실행하지 않았다.

## 전체 읽기 범위

정확 Git 객체의 변경7파일 전체 diff와 최종 파일을 읽었다. root WT HEAD가5823079임을 확인하고 old/new 각각 git archive를 별도 C 임시 폴더로 추출했다. AGENTS 규칙, marker API→repository 및 getMarkerSummaries→request 실행 경로, DTO 변환과 AwareDateTime을 함께 확인했다.

1. frontend/components/weather-map.tsx
2. frontend/components/weather-map.test.ts
3. src/kortravelweather/repository.py
4. tests/test_marker_cpu.py
5. tests/test_marker_generic_plan.py
6. tests/test_marker_pair_query.py
7. tests/test_marker_winner_hydration.py

정확 Git blob/파일 SHA는 함께 동결한 checkpoint에 기록했다. 제품·의존성·schema·수집·retry·health 변경은 이 delta에 없다. git diff --check도 통과했다.

## Frontend 큐·취소·캐시 검토

weather-map.tsx119–120의60/3 상수는 그대로다. 370–415의 effect에서 pendingLocations를 한 번 partition한 유한 chunks를 공유한다. nextChunk 선택/증가 사이에 await가 없으므로 단일 JS 실행 흐름에서 worker 중복 배정이 없다. worker는 자신의 요청 하나를 await한 뒤 다음 chunk를 가져간다. Promise.all은 worker최대3개를 만들며 wave barrier를 제거했지만 동시 상한은 늘리지 않는다.

cleanup은 cancelled=true를 먼저 설정한 뒤 AbortController.abort를 호출한다. await 뒤 publication guard와 다음 while 조건이 모두 같은 cancelled를 확인하므로 취소를 무시한 fetch가 성공해도 cache/state에 쓰거나 다음 chunk를 시작하지 않는다. getMarkerSummaries의 signal 전달과 request 함수는 변경되지 않았다. HTTP/DTO 처리 예외는 그 chunk에 한정되며 명시 retry가 없다.

완료한 chunk는 즉시 cache/state에 남긴다. 같은 refreshToken의 다음 viewport는 성공한 ID만 제외하며 실패/미완료 ID는 성공 cache가 없다. refreshToken이 달라지면 기존 세대 cleanup 후 cache를 비운다. fulfilled data만 저장하고 in-flight Promise를 다른 signal과 공유하는 구조가 없다.

265–279의 marker useMemo는 계산에서 읽는 mapLocations/selectedId/summaries를 모두 의존성에 포함한다. setSelectedId는 React의 안정된 setter다. markerState/markerTemperature/markerCondition은 시간이나 숨은 외부 상태를 읽지 않는다. 무관한 상세/제공처 상태 변경 때 같은 배열을 유지하되 선택/좌표/이름/summary 변경은 반영한다.

### 독립 actual callback 모형6조건

동결 TS AST에서 실제 effect와 marker memo 식을 추출·transpile하여 실행했다. 네트워크/브라우저는 만들지 않았다.

- 692개를 완료 순서가 뒤섞여도 총12요청, 각60이하, 모든 ID정확1회, 실제 활성 promise최대3으로 완료.
- 부분 성공60개를 즉시 보존하고 cleanup 뒤 overlap 요청은 cached ID를 제외.
- refreshToken 변경 시 성공 cache도 다시 요청.
- AbortSignal을 무시하는 늦은 성공도 cache/publication/후속 요청을 차단.
- 504에 해당하는 promise 실패는 성공 cache로 넣지 않고 retry없이 다음 chunk를 시작.
- cleanup 직전 resolve의 microtask race/list모드0작업 및 memo 참조/갱신 경계를 검사.

모형 파일과 안전 결과는 같은 review prefix의 node.mjs/node.safe.json이다. 테스트는 실제 React scheduler/MapLibre 렌더러/서버 SQL 취소까지 증명하지 않는다.

## Repository winner hydration 검토

repository.py2666–2685는 기존 candidate_ids의 top4/고정 partial-index literal/ORDER BY와 정확 PK rejoin을 유지하면서 선택5열(location_id,metric_key,known_at,source_record_key,value_id)만 읽는다. 2688–2764의 Python tuple 비교와 관측 우선→missing pair만 fallback 계산은 그대로다. 따라서 DB collation이 반환한 같은 top4 집합 안에서 Python이 고르는 기존 승자를 변경하지 않는다. 비ASCII source key도 SQL LIMIT1로 대체하지 않았다.

2766–2803은 Python selected 순서대로 value_id/known_at 배열을 같은 selected_keys에서 만든다. PostgreSQL 다중 UNNEST는 위치별 pair이며 곱집합이 아니다. 두 배열의 타입은 ARRAY(String)/ARRAY(AwareDateTime)로 명시되어 psycopg SQL에서 timestamptz[] cast를 확인했다. JOIN은 value_id와 known_at 둘 다 비교한다. 결과 DB 순서와 관계없이 by_key로 매핑한 뒤 selected_keys 순서로 재구성한다. 다른 known_at의 같은 UUID나 차순위 fact를 대체하지 않는다.

최종 row는 기존 select(WeatherValueRow) 및 _value_model(980–1007)을 거치므로 raw payload/source_record_key/시각/단위/식별자 전체 변환은 그대로다. alert_station/seq/window/rank/limit/복합키 rejoin/active_alert_values 블록은 변경되지 않았다. 후보가 비어 있으면 새 winner 왕복도 없다.

### 독립 검증

- archive의 새 winner hydration7회귀를 --noconftest로 격리 재실행: 7 PASS. 원 Weather conftest와 DB는 로드하지 않았다.
- 추가15모형: 기존 exact 함수와 새 exact 함수를 같은 합성 row로 실행하여 반환 전체 model_dump와 순서를 비교했다. 8위치×6metric×top4,12개 조합에서 Z/a/가/é/Ω/emoji/ß source tie, 다양한 known_at 및 raw payload, observed/fallback 혼합을 포함했다.
- 같은 UUID의 다른 known_at·다른 위치 shadow가 결과에 섞이지 않음을 확인.
- UTC와+09:00의 같은 instant가 같은 복합키를 찾는지 확인.
- 후보0은 winner쿼리0, 선택된 fact 삭제는 누락만 반영하며 다른 partition으로 대체하지 않는지 확인.
- 새 왕복의 SQL psycopg compile과 배열 길이48/48·선택5열을 확인했다.
- 실제 PostgreSQL 실행/plan/IO/JSON decode 비용은 본 리뷰에서 측정하지 않았다.

모형 파일과 안전 결과는 같은 review prefix의 python.py/python.safe.json이다.

## 한계와 적용 시 반드시 분리할 점

1. 후보가 있으면 marker repository의 정상 SQL왕복이 보통3→4로 증가한다. fallback이 필요 없으면2→3이다. 전체 ORM/JSON 읽기는 top4 후보 전체에서 승자만으로 줄지만 실제 elapsed/CPU/RSS 개선은 payload·DB plan·부하·네트워크에 달려 있다. 이 리뷰를 성능 실측 PASS로 읽으면 안 된다.
2. READ COMMITTED에서는 selection과 hydration 사이 retention 삭제가 가능하다. 새 구현은 삭제된 승자를 누락하고 다른 fact로 대체하지 않는다. 기존 한 번 읽은 ORM 결과가 그 삭제 뒤에도 메모리에 남는 시점 의미와 완전 snapshot 동일성은 없다. 소스 주석·회귀가 이 의도적 경계를 명시하며 잘못된 UUID/차순위 치환은 하지 않는다. 동시 update/delete가 없는 경우에만 독립 old/new DTO 전체 동등성을 확인했다.
3. UI는 692개 완료 시 chunk별 state갱신 최대12회로 기존 wave별4회보다 잦다. useMemo는 무관한 렌더를 줄이지만 summary가 바뀔 때는 marker 배열/지도 갱신이 필요하다. 이 교환점의 실제 브라우저 CPU/응답 완주는 root live 측정 대상이다.
4. AbortSignal이 브라우저 요청을 중단해도 동기 SQL까지 취소되는 것은 별도 문제다. 이 delta가 DB 실행 취소/글로벌 worker admission/HTTP timeout을 고쳤다고 주장하지 않는다.
5. 캐시 총범위와 alert 메모리 상한은 기존 계약 그대로다. 전체 service 메모리 절감률이나 CPU100% 해소를 이 소스 검토로 단정하지 않는다.

두 delta에서 신규 차단 결함은 발견하지 못했다. root의 정확 통합 HEAD 필수 fullchecks/실제 응답·성능/운영 gate는 별도로 유지한다.
