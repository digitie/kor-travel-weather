# Marker fact 복합 키 재조회

기준은 Weather98 `fd737d56a043dead878efc535a77a140cd4fd412`다. 이 변경은 fact 선택의 정확성과 partition key 보존을 보강한다. 운영 지도 504의 해결은 아직 검증되지 않았다.

## 확인한 구조

지도는 위치 60개씩 최대 3요청을 병행하고 API는 최대 500개의 명시 location_id만 받으며 중복 ID를 제거한다. 빈 ID는 fact 조회를 수행하지 않는다. 기존 observed/fallback은 metric별 최신 후보를 LATERAL로 제한한 후 value_id만으로 fact를 다시 읽고, alert window의 최종 fact JOIN도 value_id만 사용한다. 그러나 fact의 실제 primary/partition key는 `(value_id, known_at)`이다.

## 최소 변경

후보 관계와 alert 제한 관계에 known_at을 보존하여 fact를 두 키로 JOIN한다. 기존 metric allowlist·observed 우선/fallback·known/source/value tie-break·alert station/sequence grouping·명시 valid_until 및 max-age·위치별 한도·DTO와 요청 위치 순서를 유지한다. raw/source/upsert/수집 로직, API timeout/UI 병렬도/retry/schema/index는 바꾸지 않는다.

known_at 동등 JOIN이 있어도 planner가 항상 모든 partition을 제거한다고 주장하지 않는다. 같은 UUID와 서로 다른 known_at은 DB primary key가 허용하므로 선택한 pair의 정확성을 회귀로 검증한다. 요청 외 위치를 가진 UUID 충돌 행은 이 DB 경계의 합성 반례이며, 운영에서 그런 행이나 오응답이 관측됐다는 의미는 아니다.

## 검증

- 새 SQL 구조·빈 배치·실제 PostgreSQL의 observed/fallback/alert 복합 키 회귀 7개 통과. 별도 원본 Weather98 archive에서는 행 혼입 반례 3개가 모두 실패하고 수정본에서는 모두 통과했다.
- 전용 UUID PostgreSQL DB에서 전체 Python 539개 통과, 49 warnings. 원래 저장소·다른 시험 DB는 변경하지 않았다. 첫 전체 시도는 이전 Common pin의 다른 venv를 재사용해 collection 단계에서 실패했으며, 자체 venv의 frozen lock 설치 후 전체를 다시 통과했다.
- Ruff, frontend type-check, 189개 frontend 테스트 통과. 새 DTO나 프런트엔드 변경은 없다.
- 초기 PostgreSQL 시험은 source의 canonical fetched_at 기본값과 fixture의 known_at 기대가 달라 실패했다. fixture에 fetched_at을 명시해 바로잡았으며 제품 시간 처리 정책은 변경하지 않았다.

## 실제 운영 읽기 측정과 남은 범위

현재 실제 catalog에서 얻은 대표 위치 60개와 동일한 aware 시각으로 old/new SQL 3개를 구성했다. 원 R19 실패 요청의 query ID 목록이 보존되지 않아 정확한 실패 배치라고 부르지 않는다. fallback은 첫 조회가 모두 비었다고 놓은 상한 모형이며 실제 missing pair는 더 적을 수 있다.

운영 읽기 전용 EXPLAIN에서는 alert의 추정 total cost가 91,766에서 5,479로 줄었고 observed/fallback의 추정 행 수도 줄었다. 이 값은 실행시간이 아니다. 이어 각 문장에 8초 상한을 둔 읽기 전용 EXPLAIN ANALYZE 결과는 다음과 같다.

| 경로 | 원본 | 수정본 |
| --- | --- | --- |
| observed | QueryCanceled 57014 | QueryCanceled 57014 |
| fallback 상한 모형 | QueryCanceled 57014 | wall 5.853초, planning 794ms, execution 4,324.8ms, 410행 |
| alerts | QueryCanceled 57014 | QueryCanceled 57014 |

이는 개별 SQL 표본이며 API 전체·ORM 변환·세 요청 동시 부하·live UI 성공이나 CPU/RSS 감소를 증명하지 않는다. 원 R19 marker 504 실패는 그대로 보존한다. 남은 partition/index 필터, LATERAL 반복, 특보 window 및 동시 DB 부하는 별도 후속 변경에서 측정한다. 이 PR에 timeout 상향이나 추가 쿼리 재설계를 섞지 않는다. 배포·live UI·최종 독립 리뷰 결과는 별도 운영 게이트다.
