# Weather103 P2 postfix 독립 검토 A

판정: SOURCE_PASS. 기존 W103-A-P2-01은 exact HEAD에서 CLOSED. 본 독립 postfix 검토에서 새 P0/P1/P2 없음. 작성자의 후속 partition statement_timeout 복원 수정은 이 HEAD에 포함되지 않으며 새 HEAD 별도 delta 검토가 필요하다.

대상 PR103: 6fa7b5947dda7dfb486de47f08921485cf0a6e62. 초기 검토 f64ee6e4c93700cd5518475562d07cd82481a6ae, 제품 기준 916715d7ca9414e259adb60ea22c73543a891af6. 검토자 /root/review_finish_a. 원본·다른 작업자 파일 변경0, 다른 검토자의 원문/모형 미열람.

## 범위 및 계보

초기 FULL12 검토 weather103-shortterm-retention-review-a-r1-20261016.md SHA aa0caac3689bc56153b2f3ba8d9f1d511398375b1f26f3b6f685278976208404를 유지하고 f64→6fa 전체9파일 차이를 읽었다. 최종 기준 전체16파일 checkpoint SHA f82f5b765009d784f1cf0f82ecd11f48b2e87ca1528c823be1ce62f339d27a61의 각 Git blob16개를 직접 SHA 대조, Python13개 compile했다. AGENTS와 repository의 ingest/resolve/source alias·잠금/기존조회/insert/retention/replay 호출 경계를 읽었다.

제품 실질 변경은 repository.py와 retention.py다. 나머지는 ADR072/data-model/사용 가이드와 회귀 tests다. 기존 future partition 시험에서 retention_bounds seed 우회를 제거했고 API revision 시험은 상대시각으로 보존창 내 상태를 만든다. Dagster collector wrapper 시험에서는 실제 수집기가 아닌 새 retention asset도 제외한다. 제품 API/DTO/frontend/공식 migration/DEFAULT DDL 변경은 없다.

## 기존 지적의 실제 closure

원 지적은 valid_at이 미래여도 원 수신 clock이 retention gap이면 새 fact를 버리고, 이미 존재하는 source의 늦은 replay도 horizon 이전이라는 이유로 버리는 것이었다.

새 호출 순서는 배치 catalog 두 조회 공유 → source identity/alias 및 원 clock 해석 → 기존 복합키 조회 → readmit_retained_short_session → 일반 forgotten-replay 처리 → 기존 immutable insert/동일 transaction projection이다.

- 정확한 KMA 단기예보이며 valid_at >= now-6h인 fact만 보호한다. 다른 원천·domain·style·NULL·기한 지난 값에는 예외가 없다.
- 원 clock이 DEFAULT의 floor 이전이거나 실제 dated partition 안이면 그대로 둔다. isolated old partition 때문에 나중 gap을 놓치지 않는다.
- 보존할 원 fact가 없고 원 clock이 저장 불가능할 때만 그 배치의 실제 admission time으로 known_at을 바꾼다. 현재 clock도 저장할 partition이 없으면 ValueError로 실패한다.
- 기존 source의 retained row를 value_id별 최초 known_at 순서로 찾고, 그 row의 정확 pair를 재사용한다. 기존 원 pair가 있으면 우선 보존한다.
- source.fetched_at/source_record_key/원 WeatherValue/collected/issued/valid/payload는 새 재입장 시 그대로이며 기존 row 재사용 시 row의 원 collected_at을 쓴다. source alias 검증·source별 advisory transaction lock은 앞단에 유지된다.
- 같은 배치 및 다음 replay의 복합키는 한 쌍이고 typed/payload 불일치는 기존 immutable 검사로 거절한다. 일반 replay·일반 provider는 이전 경로를 유지한다.

## 독립 실행

weather103-retention-postfix-model-a-r1-20261016.py는 immutable git archive에서 실제 함수를 import했다. SQLAlchemy create_engine을 즉시 거절하도록 대체하여 실제 DB 접근을 차단했다. author conftest/PG fixture/SDK는 import하지 않았다.

새36조건 + 기존 transaction/SQL/policy23조건 PASS, EXIT0:
- 최초/replay gap 반례를 readmit→insert→일반 replay 경로로 연결하여 과거 skip/drop에서 현재 수용 확인.
- floor 직전/정확경계, dated 하한/상한, DEFAULT, isolated partition/gap, cutoff equality/먼 미래, provider/domain/dataset/style/NULL 제외.
- 현재 partition 부재 실패, 최초 retained pair 재사용/반복 replay 추가0, 같은 배치 중복1쌍, payload/typed 충돌 거절.
- DISTINCT ON + value_id/known_at 정렬의 실제 PostgreSQL SQLAlchemy compile, source1001개+중복의 조회 배치1000/2 확인.
- 이전 exact-composite pointer→fact 삭제 순서, bind/식별자 검증, rollback, readonly/dry-run, advisory busy, SKIP LOCKED remaining, partition 보존 True/unknown 거절, 부분 batch failure 기록을 재검증.

모형 SHA e821479c51333ea7ce600fdd3733dbee9f42dac4f91671b8724bb1397dd50851.
모형 safe JSON SHA 04da65571f10cea74a8eacbd0b2a1b172f594ebb54c9552786151041dda18268.

## 한계 및 후속

이는 소스·실제 함수 기반 inert session/SQL compile 검토다. 실제 PostgreSQL 동시 ingest/partition DDL/rollback 및 full suite를 본 검토자가 재실행하지 않았다. 작성자 PG/full 결과를 독립 실행 수에 합산하지 않았다. 기존 배치 budget은 cooperative이며 단일 SQL/연결 대기를 포함한 hard wall 보장은 아니다. 미래를 포함한 partition 보존은 같은 partition의 다른 값도 함께 보존할 수 있다. 운영 backlog 완주·스케줄 실행·CPU/저장량 효과·운영 반영은 별도 실측 대상이다.

작성자가 뒤늦게 지적한 partition 조회 statement_timeout5s의 후행 DDL 전파는 별도 수정 예정임을 parent가 전달했다. 이 보고서는 그 미발급 새 소스를 승인하지 않는다. 기존 statement_timeout 원값 보존/성공 시 복원/오류 rollback 여부를 새 exact HEAD에서 추가 검토해야 한다.

DB/SDK/SSH/HTTP/Docker/운영 호출 및 제품 수정0. 소유 실행 도구 모두 terminal.
