# Weather marker winner hydration 독립 FULL 리뷰 A

## 판정과 고정 대상

SOURCE_PASS, 열린 P0/P1/P2 = 0. 제품 source 동등성 검토 결과이며 실제 성능/운영 UI 완료 승인이 아니다.

- 대상 HEAD049488fb9453e7c7648023b3478da9a48ea4cf7d, base0acb11fc94d1427f7e920d3feed8a69fac515c21.
- D WT /home/digitie/dev/kor-travel-weather-codex-marker-winner-d-20261016의 원본 변경0.
- source checkpoint weather-marker-winner-source-d-r1-20261017.checkpoint.json SHA fe8d01dfa8a3a30d95473545e8efbef83dc1dbf0c489c3a2be69a0ead1e52edb 직접 확인.
- 검토 FULL5: repository.py, test_marker_cpu.py, test_marker_generic_plan.py, test_marker_pair_query.py, 신규 test_marker_winner_hydration.py. 전체 diff와 각 시험/실제 marker 메서드, 모델 변환/PK/시각형/immutability, API marker 호출·DTO·분류, 경보 raw reducer 관련 문맥을 직접 읽었다.
- 본인 frontend PR105 작성분은 이 독립 리뷰 대상/집계에 포함하지 않는다. 다른 reviewer 원문·author 모델/결과 파일은 열람하지 않았다. 작성자 handoff는 source/tradeoff 안내로만 읽고 아래 검증을 새로 수행했다.

## 핵심 검토 결과

1. repository.py:2666–2685 후보 outer SELECT만 location_id/metric_key/known_at/source_record_key/value_id 다섯 필드로 줄였다. top4 lateral SQL/observed 우선·missing pair·고정 partial predicate·기존 Python tuple 비교가 보존된다. SQL collation 최대값이나 limit1로 바꾸지 않는다. 실제 SQLAlchemy Row attribute를 사용하는 자체 모델에서도 동등했다.
2. 2769–2795 승자 조회는 VARCHAR[]와 TIMESTAMP WITH TIME ZONE[]를 한 unnest table로 zip한다. 두 배열은 동일 selected_keys에서 만들고 같은 순서를 유지한다. value_id와 known_at 모두 JOIN하므로 같은 UUID의 다른 partition을 섞지 않는다. 요청 값은 bind이며 hostile ID를 SQL 문자열에 끼워넣지 않는다. AwareDateTime의 naive 거절도 그대로다.
3. 2796–2803은 전체 ORM 조회의 DB 반환 순서를 selected_keys 삽입 순서로 되돌린다. winner 전체 행만 기존 _value_model로 변환해 payload 원형·source_record_key·시각·숫자/normalization을 보존한다. 기존 API/DTO/models.py/alerts.py는 Git blob byte 동일하다.
4. 2804 이후 alert SQL/reducer/return은 원문 byte 동일하다. JSON stn/seq/제목/release/expiry를 제거하지 않는다. 상속된 active_alert_values의 실행 시각 사용 등 기존 정책도 변경하지 않는다.
5. 후보 scalar와 hydrate 사이 추가 READ COMMITTED 경계는 실제 의미 변화가 있다. retention이 고른 정확 winner를 삭제하면 해당 metric은 빠진다. 다른 partition/차순위나 새로 들어온 fact를 대신하지 않는다. 이미 ORM을 읽던 이전 구현은 삭제 전 내용을 반환할 수 있었다. snapshot isolation/전부 단일시점 응답이라는 신규 보장은 없으며, 이 tradeoff는 작성자가 명시했고 현재 API도 기존 다중 SELECT 원자 snapshot 계약이 없다.
6. selected가 있을 때 전체 fact 조회1회가 추가된다. 빈 후보는 이 쿼리를 생략한다. repository 메서드 바깥 변화0, DB 예외를 빈 성공으로 삼키는 catch/재시도/시간 예산 변경0이다. 자체 네 단계 statement 예외 주입에서 예외가 전파되고 session 종료가 호출됐다.

## SQL·메모리·동시성 한계

후보는 위치×6 metric×최대4의 기존 상한이며 승자는 위치×6 이하이다. API는 최대500 unique 요청을 넘지 않게 제한하므로 정상 경로 승자 배열은 최대3000 pair다. 직접 repository 호출에 별도 새 상한을 추가한 것은 아니다. 후보의 raw JSON/전체 ORM을 읽지 않는 감소 경로는 소스상 확인되지만 scalar 목록·selected·winner ORM·by_key의 목록/사전은 동시에 생존한다. 메모리 RSS/peak 절감량 또는 CPU19% 등 이전 다른 변경의 수치를 이 변경 성과로 합산하지 않는다.

새 winner JOIN은 정확 PK 재조회지만 실제 PostgreSQL의 generic/custom plan, partition pruning, 실행시간/추가 round trip 비용은 이번에 측정하지 않았다. 기존 generic PG 시험의 빈 Session double 경로는 winner 쿼리를 만들지 않으므로 해당 시험만으로 새 쿼리 실측 계획 검증을 대신할 수 없다. root의 대표60개 실제 쿼리·raw/DTO 비교·전체 통합 회귀와 같은25초 UI gate가 후속 gate다.

동시 삭제/추가는 단일 스레드 합성 세션 사이의 상태 변화로만 검증했다. 실제 concurrent transaction/DDL/retention lock·PG driver array decoding·statement_timeout은 미실행이다. fact UPDATE는 원래 immutability trigger가 금지하며 이 변경도 그대로다. DELETE 후 동일 PK 재삽입의 별도 실제 race까지 보장하지 않는다.

## 독립 실행한 검증

자기 immutable archive /home/digitie/dev/weather-marker-winner-review-a-20261017/candidate 및 독립 script independent.py를 만들었다. 기존 venv의 Python/dependency를 읽되 archive src를 sys.path 최우선으로 사용했다. psycopg.connect를 실패 함수로 막고 원 conftest는 로드하지 않았다. 실제 engine/session/DB/SDK/HTTP/Docker/SSH/private/auth 접근0.

12조건 EXIT0 (그 중 전체 WeatherValue JSON 동등성은 seed 고정 무작위60시나리오):

- observed/fallback/top4/tie/source Unicode/6metrics·3locations의 old/new 전체 WeatherValue, payload, lineage, 시각과 반환 순서 동일.
- 실제 SQLAlchemy Row 다섯 필드에서 selection 수행.
- 동일 UUID 다른 known_at/unrequested location 배제.
- 삭제된 exact winner 누락 및 다른 partition 비대체.
- 후보 조회 후 신규 더 최신 fact 삽입 시 선택 키 비대체.
- active alert raw 및 만료 release tombstone의 old/new 결과 동일, 빈 후보 winner 조회0.
- observed/fallback/winner/alerts 네 statement 각각 예외 전파+session 종료.
- typed array zip/정확 두 PK JOIN SQL compile, ID 인젝션 음성, statement cache 구조 재사용.
- 양 candidate lateral 전체 SQL/top4/partial literal/ON clause old/new 동일.
- 전체 alert SQL old/new 동일.
- aware bind 및 naive timestamp 거절.
- empty/invalid limit이 session을 열지 않음.

결과 원문 weather-marker-winner-review-model-a-r1-20261017.log 및 .safe.json. 이 12조건과 60시나리오를 author23시험·full suite·실제PG 성공으로 합산하지 않는다. 미실행: 전체pytest, 실제PG, 실제API/브라우저/R24 재실행, 빌드/배포/성능 측정.

## 다음 단계

독립 두 번째 제품 검토와 root 통합 시험/실제 계획·동일 raw/DTO 확인을 거친다. frontend worker queue와 합칠 경우 요청 동시상한은3이지만 CPU 부하가 연속될 수 있으므로 backend 단독 CPU/wall 및 합친 12배치25초 gate를 별도 기록해야 한다. 이 원문은 SOURCE_PASS만 고정한다. 제품 수정 및 다른 작업자 변경 되돌림0, 소유 도구 모두 terminal.

Safe checkpoint SHA256: 9267677284daef909f555e470f0d4cd1f619236987a3983cc3eca8c3d6c53745
