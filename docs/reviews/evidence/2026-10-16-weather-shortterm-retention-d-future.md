# Weather103 미래 재입장 postfix 독립 리뷰 D

판정: SOURCE_PASS. W103-D-P2-01 FIXED. 새 열린 P0/P1/P2/P3=0.

## 정확한 대상
- 최초 FULL 대상: f64ee6e4c93700cd5518475562d07cd82481a6ae; 최초 REQUEST_CHANGES 원문은 weather103-shortterm-retention-review-d-r1-20261016.md(SHA84dc88513da34b47ff78b117d7cffeb9ec6809a63fc5089e9c45ea76c257ec85) 그대로 보존.
- 최종: 6fa7b5947dda7dfb486de47f08921485cf0a6e62
- 최초→최종 전체 9파일 delta SHA256: 9304adf8d4fe356a066bf29bf0f119e6f404bc78f4ee5a9a3059bbc94eaeb202.
- 최초 FULL12 및 이번 delta9를 합쳐 base101 대비 전체16 경로를 계승한다. peer 리뷰 미열람/공유 source 수정 0.

## Closure
정확한 KMA 단기예보의 valid_at >= now-6h인 경우에만 expired-known window 제외와 잊힌 replay 재날짜 경로를 우회한다. 먼저 원 fact의 정확한 pair를 보존하고, 원 pair가 없으면 기존 earliest retained value_id row를 우선 재사용한다. DEFAULT 수용(clock<floor)/dated partition에 들어가는 원 clock은 변경하지 않으며 실제 gap일 때만 admitted_at을 사용한다. 새 시각도 수용 불가하면 실패한다. 원 value/source/issued/valid/raw/collected 필드를 수정하지 않는다. 문서에 bitemporal 예외와 재입장 known_at 의미를 명시했다.

배치당 catalog 두 조회를 공유하고 replay 후보 ID만 1000개씩 DISTINCT ON 조회한다. source alias/immutable 비교/위치 잠금/복합키·projection 원자성 경계를 우회하지 않는다. 최초 코드의 과거 source에 매달린 미래 valid_at 반례를 같은 실제 _insert_value_session 호출로 재실행했으며 이제 True insert/그다음 False replay다. 값 충돌은 계속 ValueError다. 새 재입장 gap 경로와 정상 DEFAULT/dated 원 clock 보존, 다른 provider 무변경, 1001개 replay 후보의 정확한 2회 조회도 자체 모형에서 통과했다.

## 직접 시험과 한계
- final immutable archive, 원 conftest 완전 제외: 순수 unit **46 PASS / 0.42초**.
- 자체 실제 함수 모형: 원 P2 negative closure/동일 배치 replay·충돌/clock·원 필드/1001 조회 배치 PASS.
- 로그: weather103-review-d-pure-r2-20261016.log, weather103-review-d-model-r2-20261016.log.
- 모형: /home/digitie/dev/.weather103-review-d-6fa7b59/own_future_closure_d.py.
- 실제 PG/partition routing/FK·잠금·rollback 시험은 본인이 실행하지 않았다. 저자의 85 PG·전체시험 숫자는 독립 검증 수에 합산하지 않는다. 실제 DB/SDK instance/HTTP/provider/SSH/운영 호출 0.
- 기존 6시간 cutoff/미래 무제한/유한 정리 budget/과거 partition의 미래 보호 계약을 유지한 source 승인이다. 초기 backlog 완주·운영 저장 공간 개선·wall-clock SLA는 증명하지 않는다.
