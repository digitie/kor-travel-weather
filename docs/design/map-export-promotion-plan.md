# Weather Map export 단계3 구현 계획

- 상태: 단계3A의 nonempty 내부 lifecycle 범위는 PR139로 구현·검증·머지됐다. 아래 최초 계획과 차이는 [최종 수용 기록](../reports/2026-10-23-map-export-plan-barrier.md)을 따른다. 정상0 증거·실제 adapter/collector 통합·3B·비용/frozen·실제 데이터 API는 미완료.
- 기준: `/mnt/f/dev/kor-travel-weather-map-perf`, `91801d4593fad3b95aafd7456a5d94b56dc23130` (PR136 머지).
- 정본: `docs/design/map-service-exports.md` §3.1~3.3, 현재 `export_receipts.py`, `export_artifacts.py`, repository 수집/종료 hook.
- 코드 조사 기반 계획이다. 운영·DB·수집 설정 변경과 성능 실측을 수행하지 않았다.
- **COW 합산 비용 exit gate는 통과하지 않았다. 이 제안은 collector 활성화·API 데이터 공개·전국 초기 구축 승인이 아니다.**

## 권고 결론: 첫 PR은 계획·봉인·terminal 검증까지만

단계3을 한 PR에 collector 전체 opt-in, artifact 재구성, generation 물리화, API frozen pagination까지 넣지 않는다.
가장 작은 첫 PR(3A)은 DB lifecycle과 bounded 검증을 구현하되 공개 head를 만들지 않는다. 후속 3B에서 동일 검증 결과를 입력으로 성공 head CAS와 변경 group 연결을 구현한다. 3A만 완료한 것을 단계3/W-MAP-02 완료로 표시하지 않는다.

3A의 반환 결과는 `NOT_READY / ELIGIBLE / REJECTED`이며 `PUBLISHED`라는 이름을 쓰지 않는다. collector는 계속 opt-in OFF, 기존 service endpoint는 계속 EXPORT_NOT_READY다. 기존 수집 raw/fact/성공 의미를 바꾸지 않는다. native/Dagster를 모두 검증할 수 있는 작은 synthetic fixture와 수동 내부 reconciliation entry만 추가한다.

단일 PR로 head CAS까지 필요하면, 3B의 내부 metadata-only generation/head를 함께 구현할 수는 있다. 그러나 단순 receipt chain을 완성된 W1/W2/W3 snapshot이라고 부르지 않는다. 아래 그룹 교체·winner·frozen manifest가 준비되기 전 public API와 연결하지 않는 gate를 코드와 테스트로 유지해야 한다.

## 1. 현재 코드가 이미 보장하는 것과 부족한 것

이미 있음:

- canonical alias와 정확한 `(value_id, known_at)`의 persisted point를 receipt에 묶는다. insert0/CAS0 replay도 후보다.
- raw/fact와 chunk receipt는 같은 transaction이다. 파일은 commit/pool 반환 뒤 fsync/rename한다.
- 파일의 digest/byte/point count, 일반파일·동일fd를 검증하며 chunk 5000point/8MiB 상한을 둔다.
- 실패표시의 잠금 경합까지 savepoint/bounded transaction으로 격리한다.
- attempt 생성 시 running SyncRun과 native/Dagster identity를 확인한다. 모든 capture source record는 같은 non-null run을 요구한다.

아직 없음:

- `planned_sources`는 예상 source 집합일 뿐 예상 chunk 집합/segment 완료 증거가 아니다. 실제 chunks에서 count/digest를 역산해서 seal하면 300중32개도 완결로 오인한다.
- 현재 owner_id는 attempt capability다. 갱신되는 authoritative scope owner epoch/current attempt registry는 없다. timestamp lease만으로 owner fencing이 생기지 않는다.
- `SyncRunSourceRow`는 run/source 연결일 뿐 모든 group/accepted point의 완결 증거가 아니다.
- `SyncRun.status=success`만으로 Dagster SUCCESS를 증명하지 않는다. 기존 `terminal_worker()`는 `is_finished` bool이라 실패/취소도 True다. 그대로 재사용하면 안 된다.
- `finish_sync_run()`은 이미 terminal이면 no-op이고 `publish_and_finish()`는 CAS 실패면 예외다. 호출자의 희망 status가 아닌 반환된 실제 terminal row를 검사해야 한다.
- 모든 파일이 있는지, 전체가 안전한지, 성공 head CAS/변경 로그/GC reader pin은 아직 없다.

## 2. 최소 schema와 상태 기계

### 3A: migration0024에 lifecycle만 추가

기존 `weather_export_attempts`를 재사용하고 상태를 유지한다: `preparing -> sealed`, 또는 `failed`. published 여부는 별도 generation으로 표현한다. 재실행은 같은 attempt/owner와 같은 seal digest에서만 멱등이다.

추가 필드 예시:

- `plan_schema_revision`, `plan_closed_at`, `expected_plan_digest`, `sealed_at`, `seal_digest`.
- `owner_epoch` (아래 owner registry와 대응). 실행 mode/run/orchestrator identity는 변경 불가.
- 검증 진행 cursor/다음 retry 시각은 별도 작은 worker state에 두어 sealed 내용 자체를 수정하지 않는다.

새 `weather_export_expected_chunks`:

- PK `(attempt_id, chunk_key)`.
- `source_scope_key`, `segment_ordinal`, `segment_count`, `expected_point_count`, `expected_input_digest` 또는 accepted point key/value digest, `completeness_kind`.
- scope가 여러 개 섞인 chunk면 mapping을 명시한 child table/고정 bounded JSON을 사용한다. `covered_sources` union만으로 segment 완결을 판단하지 않는다.
- `segment_count > 0`, ordinal 범위, 중복 segment 방지 unique constraint.
- 정상 빈 source도 명시 empty proof를 가진 0point chunk/계획을 갖는다. 원천 오류·parse reject·아직 fetch하지 않음은 정상0과 다르다.

새 `weather_export_scope_owners` (또는 동등한 기존 상태 테이블의 명시 컬럼):

- PK stable scope ID, `owner_epoch`, `active_attempt_id`, 선택적 lease.
- 단순 원천 scope는 provider/dataset/style/안정 collection entity(예: nx/ny grid)다. 회전 slot 시각/실행 순서 번호를 영구 scope identity로 사용하지 않는다.
- 한 attempt가 여러 scope를 소유하면 stable key 정렬 순서로 row lock을 취한다. export 자체의 ownership이며 원천 collector 전체 동시성 한도를 넓히지 않는다.
- fencing은 변경 가능한 registry의 current epoch/attempt와 대조한다. lease가 지났다는 이유만으로 실제 살아 있는 Dagster run을 탈취하지 않는다.

새 `weather_export_terminal_evidence`:

- PK attempt_id. mode/run/orchestrator ID/owner_epoch/seal_digest, `terminal_status`, terminal finished time, `checked_at`.
- native는 exact owner/run의 실제 SyncRun success를 검증한다.
- Dagster는 exact `orchestrator_run_id`의 `DagsterRunStatus.SUCCESS`를 검증한다. missing/timeout/DB error/unknown/running은 pending; FAILURE/CANCELED는 rejected다.
- evidence를 기록할 때 다시 attempt/SyncRun/owner를 확인한다. 조회 오류를 native fallback 또는 성공으로 바꾸지 않는다.
- SyncRun 보존/삭제와 export 증거 생존 기간을 FK cascade로 묶지 않는다. 아직 terminal 증거가 없는데 SyncRun이 삭제됐다면 fail closed이며 묵시적 성공은 금지한다.

계획의 정확한 schema를 더 줄이려면 첫 PR에서 한 attempt/단일 안정 source scope만 지원하고 복수 scope는 명시적으로 거부할 수 있다. 지원 범위 축소를 숨겨 전체 KMA 완료로 표시하지 않는다.

### 3B: 성공 generation/CAS

- `weather_export_generations`: immutable generation ID, attempt/seal/terminal evidence, parent head ID, scope/schema revision, 실제 source clock, 확정 순서, changed-group digest.
- `weather_export_generation_groups`: 이 generation에서 바뀐 안정 group key와 artifact/segment hash 또는 tombstone만 저장한다. 1.8M 값 전체를 복사하지 않는다.
- `weather_export_heads`: stable scope별 current generation ID, CAS version, 승인된 source revision/clock와 health revision.
- change watermark와 generation/head CAS는 같은 transaction으로 전진한다. 각 scope의 실패/마지막 성공은 분리한다.
- generation을 자동으로 과거 상태로 UPDATE하지 않는다. 오직 새 immutable generation과 작은 head pointer를 만든다.

## 3. collector hook: 계획의 근거가 독립적이어야 한다

1. `start_sync_run()` 직후 실제 selection의 안정 source roster/입력 hash를 확정한다. provider/dataset/style, 실제 KMA grid/중기지역/특보 event source를 분리한다. 명시 disabled는 계획에서 제외하되 catalog/config revision에 남긴다.
2. 원천 응답이 정상·완결됐다는 provider 증거를 통과한 뒤, bounded normalization 결과로 deterministic segment 계획을 만든다. payload 최대치/normalizer 상한은 유지한다.
3. expected segment 계획은 해당 receipt 쓰기 전에 고정한다. 전체 source roster는 fetch 전에 고정하고, 각 source의 segment 계획은 해당 응답/정규화 완료 후 닫을 수 있다. 이 2단계 모델을 명시하여 아직 응답하지 않은 source를 계획에서 지우지 못하게 한다.
4. `ingest_batch(export_capture=...)`는 계획된 chunk_key·source·accepted count/digest에만 대응한다. alias 후 canonical key로 검증한다. `values_loaded`나 current pointer CAS count는 완결성 분모가 아니다.
5. 마지막 publisher/normalizer가 전체 expected source와 segment의 정상 처리 여부를 확인해 plan close marker를 기록한다. 실제 receipt 목록을 그대로 expected로 복사하는 것은 금지한다.
6. seal은 expected/actual exact set, source roster digest, 중복/누락/retire proof를 검증한다. 정상0과 retention으로 accepted0인 경우도 explicit complete 의미를 구분한다.
7. `finish_sync_run()` 또는 `publish_and_finish()`의 기존 결과를 남긴 후 수집 job은 종료한다. 성공 승격을 기다리지 않는다. export hook 오류는 원천 success를 failed로 재해석하지 않는다.
8. `publish_and_finish()` 경로에도 receipt opt-in을 연결할지 명시적으로 결정한다. `ingest_batch()`만 수정하면 KMA 최종 publish의 source-only/empty 완료 증거를 빠뜨릴 수 있다. 최종 raw transaction의 기존 원자성은 유지한다.

3A는 이 collector 통합을 한 번에 활성화하지 않는다. explicit adapter interface/시험만 만들고 운영 OFF를 유지하는 것이 작은 PR이다. seal이 fact transaction 안 파일 읽기까지 실행하거나, 파일 성공 뒤 raw commit을 하는 순서로 되돌리지 않는다.

## 4. 봉인과 terminal reconciliation 순서

- DB에서 bounded 계획/receipt page와 immutable manifest token을 읽고 연결을 반환한다.
- token은 attempt/epoch/plan revision/정확 receipt digest 목록에 묶는다. plan/chunk는 close/seal 뒤 변경 불가다.
- 파일을 chunk 하나씩 동일fd로 끝까지 검증한다. 검증한 bytes/digest/count와 receipt를 비교한다. 한 tick은 예컨대 32chunk 또는8MiB·시간예산 중 먼저 도달하는 상한에서 멈추고 검증 cursor를 남긴다. 이는 staging progress일 뿐 부분 seal/publish가 아니다.
- 파일 I/O·Dagster instance 조회 중 DB transaction/row lock을 잡지 않는다.
- 마지막 짧은 DB transaction에서 attempt/owner/plan/receipt revision이 그대로인지 확인하고 `sealed`로 CAS한다. 다른 owner/계획 변경이면 검증 결과를 폐기한다.
- Dagster terminal 확인은 수집 job 밖의 별도 bounded sensor/내부 CLI tick이다. 기존 sync recovery의 bool callback과 분리한 success-specific adapter를 사용한다.
- native도 returned SyncRun.status가 정확히 success이고 mode/owner가 일치해야 한다. partial/skipped/running/failed는 성공으로 치환하지 않는다.
- DB row lock 순서와 lock_timeout/statement_timeout을 정한다. 각 외부 terminal 조회에 남은 예산을 넘지 않는 timeout을 사용한다. timeout된 thread가 나중에 쓰지 않도록 fencing을 DB CAS에서 다시 확인한다.
- bounded page는 keyset cursor와 다음 sweep을 지원해 첫 pending Dagster run 하나가 후속 성공 전체를 굶기지 않도록 한다. unknown은 pending으로 남기되 정체/age/backlog 지표로 드러낸다.

## 5. head CAS, 늦은 성공과 실패 A/성공 B

핵심 재현: A가 더 최신 current를 commit한 뒤 실패한다. B는 예전/같은 canonical point를 실제 재관측하고 insert0/currentCAS0이다. B의 terminal/plan이 성공이면 B artifact는 eligible이다. current winner를 다시 읽어 A를 가져오거나 B를 탈락시키지 않는다.

head candidate의 기반은 직전 **성공 head**이며 검증된 B immutable point/완결 group 변경만 얹는다. 같은 scope의 더 새로운 성공 C가 먼저 공개되면 expected-head CAS 실패 후 head metadata를 다시 읽고 B 변경분을 재검증/rebase한다. raw/provider/fact 전체 재조회는 하지 않는다.

정렬/승패는 두 가지를 분리한다:

- 데이터 winner: logical identity에 대한 정책/정규화 revision, 원천 issued/observed/known clock와 명시 tie-breaker. finish time이나 임의 UUID 사전순으로 의미상 최신을 만들지 않는다.
- scope 재관측/freshness: receipt의 실제 source_observed_at과 그 scope의 승인 순서. 늦게 끝난 낡은 attempt가 freshness/watermark를 뒤로 돌리지 못한다. head가 이미 더 최신이면 no-op/superseded로 기록하며 과거 scope clock을 덮지 않는다.

오래된 attempt의 다른 group 변경을 모두 버려도 안전한지는 completeness 모드별로 결정한다. 더 최신 head의 완전 authoritative replacement에 포함되는 scope라면 superseded 가능하다. 서로 다른 group의 독립 delta라면 오래된 attempt라도 아직 필요한 group을 winner 규약으로 병합해야 한다. 단일 `MAX(known_at)`나 attempt 생성시각 하나로 모든 group을 버리는 최적화는 금지한다.

retire/tombstone은 해당 source/group의 authoritative complete proof가 있을 때만 만든다. 회전 바깥 grid·다른 provider·다른 style은 삭제하지 않는다. 특보는 payload event identity를 보존하고 metric-level collapse로 합치지 않는다. 만료로 accepted0인 예보는 원천 absent와 다르며 future 시간 projection은 후속 API 단계다.

## 6. 파일 누락·crash·정본 replay

- raw+receipt commit 전 crash: 둘 다 없음, 이전 head 유지.
- receipt commit 뒤 파일 전 crash: incomplete; 파일이 없으면 seal 불가. head/watermark 불변.
- 파일 fsync 뒤 seal 전 crash: 같은 receipt/manifest로 재검증하여 진행 가능.
- seal 뒤 terminal 전 crash: terminal이 확정될 때까지 pending. SyncRun success여도 Dagster running/failed면 head 불변.
- head CAS commit 응답 유실: exact generation/attempt idempotency로 기존 publish를 확인한다. 두 번 전진하지 않는다.
- missing file 복구를 위해 terminal run을 running으로 되돌리거나 mutable current를 읽지 않는다.

현재 PR136의 same-chunk 재생은 원래 SyncRun이 running이고 canonical bytes가 정확히 같을 때 가능하다. terminal이 된 뒤 file이 사라지면 기존 `ingest_batch()`는 run 상태 때문에 거부한다. 그러므로 단계3은 terminal receipt 파일 누락을 자동 복원할 수 있다고 약속하면 안 된다. 보관된 정확 bytes가 따로 없으면 재수집/새 attempt로 복구하며 이전 head 유지다. artifact 재생이 다르면 기존 digest를 덮지 않고 reject한다.

GC가 검증 후 CAS 전에 파일을 삭제하는 race도 봉쇄해야 한다. 가장 작은 첫 PR은 GC 자체를 비활성으로 유지하고 candidate/receipt가 참조하는 파일을 삭제하지 않는다. GC 도입 시 durable validation pin/reader lease와 mark-recheck-unlink 프로토콜이 필요하다. `0400` chmod만으로 파일 존재 보존을 보장하지 않는다.

## 7. frozen pagination / GC / 비용 경계

3A는 snapshot cursor나 API head가 없으므로 frozen pagination 완료를 주장하지 않는다. 3B도 mutable current-head 매핑만 구현하고 바로 API를 붙이지 않는다.

후속 조회 설계의 필수 조건:

- snapshot handle이 scope별 immutable generation vector와 작은 group-key→hash manifest를 고정한다. 최신 head가 변해도 기존 reader 내용은 바뀌지 않는다.
- append-only delta chain을 HTTP 요청마다 무제한 재귀 순회하지 않는다. 공개 전 bounded depth/indexed resolver 또는 작은 pointer manifest checkpoint가 필요하다. checkpoint도 scalar 전량 복사를 하지 않는다.
- group 변경은 완전 group 교체/명시 tombstone이다. point 단위 patch를 완전 group처럼 다뤄 metric/target 누락을 숨기지 않는다.
- API cursor는 schema/filter/limit/bytecap revision을 고정한다. reader TTL과 durable delta checkpoint 보존은 별개다.
- head, candidate, pending validation, active reader, retained checkpoint에서 참조하는 artifact는 GC하지 않는다. 오래된 receipt 삭제와 artifact 삭제도 분리한다.
- TTL 만료는410/full restart이며 empty200이 아니다. stale health와 forecast temporal filtering/ETag는 데이터 snapshot과 별도로 계산한다.

실측 gate: 변경0/소수group/전체scope 갱신 각각 cold/warm·동일입력 최소3쌍, collector+serializer+publisher+PG의 합산 CPU core-seconds/wall/RSS/WAL/temp/I/O·buffers/ready lag/API p95를 비교한다. 기준선은 성공 증거를 가진 indexed bounded query/작은 manifest이며 mutable failed-current를 기준선으로 쓰지 않는다. 값 전체 1.8M 복사0, change0 대형 재생성0, 상한 위반0을 확인한다. 현재 이 gate의 PASS 증거는 없다.

## 8. 회귀 목록과 PR별 수용 기준

3A 필수:

1. source roster300 중32receipt, 같은count지만 source key 하나교체, segment 하나누락/중복, 계획밖chunk → seal 거부.
2. 원천 완결 정상0 vs error/미fetch/normalizer reject vs retention accepted0 구분.
3. plan close 전 seal, seal 뒤chunk추가/계획수정, 같은key다른digest → 거부; 같은seal retry는 멱등.
4. alias/기존payload/replay insert0/currentCAS0의 canonical digest와 이번 source clock 보존.
5. 다른run/owner/epoch, native강등, orchestrator ID누락/불일치 → 거부.
6. native success/failed/partial/running/skipped, Dagster SUCCESS/FAILURE/CANCELED/running/missing/timeout 각각 정확 분류.
7. SyncRun success 후 Dagster failed; seal 후 cancellation/owner takeover; 느린 terminal 조회 후 epoch 변경 → 승격 eligible 불가.
8. receipt commit 응답유실/파일누락/손상/FIFO/directory/symlink, seal 전후 crash. 원천 성공과 기존 head를 보존.
9. export row 잠금50ms/실패표시 경합 회귀 유지. 파일·Dagster 조회 중 pool checkedout0.
10. 첫 pending run 뒤 eligible run이 starvation되지 않고 시간/건수/bytes budget 종료와 cursor resume 보장.

3B 필수:

11. failed A current 뒤 success B insert0/CAS0 승격; B immutable bytes가 나중 fact/current 변경으로 변하지 않음.
12. C가 head를 먼저 바꾼 CAS실패 → B rebase/no-op; 늦은 old success는 데이터/clock/watermark 역전0.
13. 같은 observation clock의 correction/tie 규약·다른group 독립delta·authoritative replacement 구분.
14. provider A 성공/B실패, grid slotA실패/slotB성공 → 실패scope head/checkpoint 유지, worst-status/minclock.
15. head와 change watermark 원자commit, 응답유실 재시도 중복generation/중복전진0.
16. baseline manifest 재사용/change0 새대형artifact0. 1.8M forecast fixture에서 scalar fullcopy0.

공개 전 후속:

17. frozen reader 중 두번head변경/삭제/예보만료, reader lease와GC경합; snapshot 내용 유지.
18. cursor TTL/체크포인트retention 만료, oldknown latecommit/동일known correction/tombstone 전달.
19. initial필수scope없음503, scope 일부만수집되면 전국ok 금지; 다른scope 성공으로 실패지우기 금지.
20. 위 COW 비용 exit gate와 API/proxy/auth/OpenAPI/TS·live UI 검증. 통과 전 collector/API opt-in OFF.

## 부모에게 전달할 최소 결정사항

- 권고 첫 PR: expected source/segment 계획 + seal exact-set barrier + fenced terminal evidence + bounded reconciliation, 공개 head/collector 활성화 없음.
- 다음 PR: 성공 head CAS/불변 변경 group references; 기존 current와 분리. 생성된 내부 head를 실제 API 완료로 표시하지 않는다.
- KMA 회전의 안정 scope, complete-group/point-delta/retire 모드, late-success winner 규약은 구현 전에 명시한다.
- 자동 terminal artifact 복구는 현재 제공되지 않는다. 새수집 복구로 제한한다.
- GC·frozen pagination·COW 합산 비용 gate는 아직 남아 있으며 이전 성공 공개를 깨지 않도록 후속으로 독립 검토한다.
