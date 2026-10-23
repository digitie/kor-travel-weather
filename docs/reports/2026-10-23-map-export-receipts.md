# Map export 단계2: canonical receipt와 불변 후보

상태: 구현·검증 중. 기존 collector는 opt-in하지 않아 아직 활성화되지 않는다. head·W1/W2/W3 실제 집합과 W4 실제 상태·W5 요약은 공개하지 않는다. 단계2만으로 W-MAP-02를 완료 처리하지 않는다.

- `weather_export_attempts`·`weather_export_chunks`와 migration0023을 추가한다. 기존 원본/이력/통계/보존 정책을 바꾸지 않으며 sync-run 삭제를 FK로 묶지 않는다. attempt는 명시 native/Dagster mode와 부모 run identity를 검증한다. ID 누락을 native로 강등하지 않는다.
- `ingest_batch(export_capture=...)`를 명시한 경우만 source alias·canonical 복합PK·실제 persisted row로 제한된 후보를 만든다. 신규 insert0건 replay/현재 포인터CAS0도 검증된 후보에 포함하고 expired None은 제외한다. Numeric14,4 표현은 실제 PG 저장값과 맞춘다. 이번 실제 source_observed_at은 최초 fact known_at과 별도다.
- receipt는 원본과 같은 transaction이며 export 오류는 savepoint로 격리한다. artifact는 commit/pool 반환 후 fsync+원자 rename한다. 파일 쓰기 실패는 원본 성공을 취소하지 않고 export attempt만 failed다. receipt만으로 파일을 준비됐다고 취급하지 않으며 이후 publisher가 전체 digest·scope·terminal 성공을 검증해야 한다.
- 중단/commit응답유실로 receipt만 있으면 동일 chunk replay로 파일을 복구할 수 있다. 다른 내용의 같은 chunk_key는 거부하고 최초 receipt를 덮지 않는다. 임의 mutable current 재조회로 파일을 복원하지 않는다.
- 파일은 chunk당5000point/8MiB,0400, hash 기반 경로다. read는 같은fd의 hash/byte/point 수를 검사하고 symlink를 따라가지 않는다. 전체 RSS가8MiB라는 의미가 아니다. 디스크/JSON 비용은 COW·수집 활성화 전 별도3쌍 실측 대상이다.

전용 PostgreSQL 집중18 PASS: replay0·alias/옛payload·실패A current 뒤 성공B·rollback·파일실패·commit응답유실/replay복구·KMA만료·DagsterID 누락/강등 거부·손상/byte/point상한·소유권/시각/scope 오류·chunk내용변경·pool반환후파일·실제Numeric일치. 첫 만료 fixture가 일반provider였던1실패는 정확한KMA dataset/domain/valid_at으로 수정했으며 보존정책을 바꾸지 않았다.

전체 회귀·migration·합산 실측·CI·두 독립 리뷰는 남아 있다. 실제 collector의 scope계획/완결/barrier·terminal성공 승격·GC/checkpoint·API활성화는 다음 단계다. 프록시/서비스 인증 계약은 PR135 main9d75b64를 유지한다.


첫 독립 적대 리뷰에서 보조 실패표시 UPDATE의 행 잠금이 원본 transaction을 취소할 수 있는 P1, 파일실패 후 실패표시 잠금 무한대기 P2, run_id 없는 원천이 다른 run receipt에 포함되는 P2를 발견했다. 실패표시를 별도 savepoint best effort로 격리하고 독립 후속 transaction에도 lock timeout을 설정했다. export는 모든 원천의 동일 non-null run_id를 요구한다. 실제 PG의 별도 connection이 attempt 행을 잠근 두 경로와 mixed missing-run 회귀를 추가하여 집중21 PASS(15.95초)다. 이 경합에서 원본2경로 모두 commit되고 ingest가 상한 안에 반환함을 확인했다. 현 collector opt-in은 계속 OFF다.


두 번째 독립 리뷰에서 digest 경로가 FIFO이면 read open이 대기하는 P2를 확인했다. `O_NONBLOCK` open 후 동일 fd의 `fstat`로 일반 파일만 허용하도록 수정했다. FIFO·directory·symlink는 소유 child5초 상한으로 검증하며 최종 집중24 PASS(29.29초)다. frontend243 PASS·type-check PASS. 전체Python/CI는 진행 중이며 현재 원천 수집에 활성화하지 않는다.
