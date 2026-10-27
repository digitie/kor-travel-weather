# KMA 단기예보의 지난 대상시각 보존정책

> **2026-10-27 현재 동작**은 아래 [상시 실패와 이어 읽기](#2026-10-27-상시-실패와-이어-읽기) 절이 정본이다.
> 그 아래의 SKIP LOCKED·매 batch 남은행 확인·예산 소진 Failure 서술은 당시 구현 기록이다.

2026-10-17 root 초기 정리에서 기존55,987 fact 삭제와 현재 경계 만료0건을 확인했다.
미래 예보8개 표본·참조/인덱스 보존 및 삭제 뒤36개 live UI 통과는
[운영 결과](reviews/2026-10-17-weather-short-forecast-initial-purge.md)에 기록한다.
아래 구현 당시 사전 상태·실패는 시점 근거로 유지하며 현재 실행 완료로 소급하지 않는다.

## 확정 요구와 범위

단기예보의 대상시각 valid_at이 현재시각보다6시간 넘게 지난 행만 정리한다.
발표시각 issued_at이나 수집시각 known_at의 age로 삭제하지 않는다.
경계와 같은 행 및 미래 예보는 보존한다. timezone-aware 입력만 허용하고 UTC로 비교한다.

정확한 대상은 provider=python-kma-api, dataset_key=weather_domain=kma_short_forecast,
forecast_style=short다. 초단기/중기/실황과 다른 provider는 포함하지 않는다.
이 문서와 후속 구현은 기존101 기준 별도 feature이며 health/Common pin/Docker 및 기존 수집주기 변경은 없다.

## 구현 계획과 참조 보존

기존 fact는 known_at으로 일별 partition되므로6시간 valid_at 정책에 partition DROP을 쓰지 않는다.
최대1000개의 정확한 복합키(value_id, known_at)를 한 batch로 선택하고,
같은 transaction에서 해당 fact를 참조하는 projection부터 지운 뒤 fact를 지운다.
다른 known_at의 동일 value_id는 보존한다. source/raw와 sync-run association은 삭제하지 않는다.
기존 append-only DELETE 허용은 transaction-local PURGE_GUC만 사용한다.
timeout/lock 실패는 rollback하며 자동 재전송·강제취소를 추가하지 않는다.

새 ingest는 batch 시작 때 경계를 한 번 계산하여 만료 fact를 새로 저장하지 않고,
기존 만료 replay도 projection으로 되살리지 않는다. source/raw와 기존 fact 충돌 검증은 유지한다.
전용 job은10분 주기로 최대100×1000행을 협력적 실행당 예산 안에서 정리한다. 예산은
`KOR_TRAVEL_WEATHER_SHORT_FORECAST_RETENTION_BUDGET_SECONDS`(기본150초, 상한180초; 잘못된 값은 거절하지 않고
경고와 함께 180초 또는 기본150초로 보정 — 모든 weather asset이 같은 설정을 읽기 때문)다.
문장당5초/lock1초다. 이어 읽을 위치가 없을 때의 첫 후보 선택만 남은 예산 안 최대45초를 쓴다.
각 문장·pool/연결 대기 때문에 전체 wall은 예산을 넘을 수 있으며 hard deadline은 아니다.
잠긴 행은 SKIP LOCKED로 건너뛰지 않고 lock_timeout 안에서 기다리며, timeout이면 그 batch만 연기한다.
지운 행이 있으면 backlog가 남아도 성공이다. 진척0이거나, 24시간 넘게 뒤처진 채 지연 상한이
7회(약70분) 연속 줄지 않을 때만 실패로 남긴다. 자세한 현재 동작은 아래 2026-10-27 절을 따른다
(2026-10-17 당시에는 SKIP LOCKED·매 batch 남은행 확인·60초 예산·예산 소진 실패였다).
현재 daemon OFF/배포·초기정리는 root의 별도 검토·운영 영역이다.

## 운영 명령

ktwctl purge-short-forecast는 기본 dry-run이며 batch 선택수만 센다. 전체 fact count가 아니다.
--execute에서만 삭제한다. --cutoff에 dry-run이 반환한 aware 경계를 고정할 수 있으나
현재시각-6시간보다 늦은 cutoff는 거절하여 미래/최근값 삭제를 막는다.
--batch-size1~1000/--max-batches1~100/--budget-seconds0초초과180초이하(기본60)를 사용한다.
operation_id/cutoff와 batch 전 intent·commit 뒤 누적 삭제수·남은행 존재를 JSON으로 남긴다.
raw/DSN/주소/credential은 출력하지 않고 실패는 error_class만 기록한다.
중간 실패는 이미 commit한 batch를 되돌렸다고 하지 않으며 자동 재전송하지 않는다.

정본 부분 인덱스 ix_weather_values_short_forecast_retention은 정확한4개 discriminator와
valid_at IS NOT NULL인 행에만 (valid_at, known_at, value_id) 순서를 제공한다.
0019 다음0020 migration과 metadata가 같은 정의이며 새 leaf에도 자동 상속된다.
기존 인덱스는 제거하지 않는다. reported 운영 표본은 기존 후보 LIMIT1000 선택에2.921초,
BitmapAnd 약88만 entry 접근, 전체 count5초 timeout이었다. 원문 private 자료를 읽거나
재실행한 결과가 아니라 root가 전달한 측정값이다. 새 운영 인덱스의 효과는 아직 미검증이다.

partition parent의 CREATE INDEX CONCURRENTLY는 지원되지 않는다. 0020은 명시적인
transactional CREATE INDEX이므로 기존 leaf 전체의 SHARE 잠금이 쓰기를 막을 수 있다.
root만 active write/drain·잠금·실제 여유·WAL/인덱스 증가 예상량을 검토한 유지보수 창에서
실행한다. 데이터 약40만 관련행이라는 추정만으로1.84GB 여유가 충분하다고 판단하지 않는다.
설치 전 count가 timeout이면 반복하지 않고 계획과 bounded 표본으로 먼저 판단한다.
실패하면 transaction과 revision은 함께 rollback되며 IF NOT EXISTS로 다른 정의를 승인하지 않는다.
commit 여부가 UNKNOWN이면 version/index 정의·valid 상태를 읽어 정산하기 전 재실행하지 않는다.
downgrade도 이 새 parent/상속 index만 제거하며 새 DML 중 잠금이 필요하므로 root가 별도 판단한다.
온라인 설치가 필요하면 parent ON ONLY + leaf별 CONCURRENTLY/ATTACH를 별도 검토해야 하며,
이 migration을 자동으로 온라인 방식이라고 부르지 않는다.

## 검증과 운영 순서

경계1마이크로초 전/같음/후, 미래와 오래된 발표의 미래행, UTC/KST 동등성,
다른 provider·dataset·style·domain·NULL valid_at,
복합키 projection 참조와 원천공유, batch/rollback을 검증한다.
기존 fact를 전부 count하거나 원천 raw/주소를 출력하지 않는다.

운영은 root만 count/정확predicate와 실행계획·소유성 확인→bounded batch 삭제→
삭제수·미래/경계/타원천 보존·참조 정합 재확인 순서로 수행한다.
이 PR은 운영 삭제·새job 실행·서비스 변경을 수행하지 않는다.


## 디스크·WAL 운영 경계

현재 호스트의 여유가 작으므로 root가 실행 전 실제 rootfs/PG 데이터 mount의 여유를
확인하고 각 batch의 row수·elapsed·남은 시간 예산과 WAL 증가를 별도로 관측한다.
SHORT_FORECAST_COUNT_SQL과 같은 정본 predicate의 whole count는 EXPLAIN부터 검토하며,
5초 읽기 예산에서 실패하면 partial/timeout으로 남기고 무제한 count를 반복하지 않는다.
dry-run 기본 matched_in_batch는 최대1000행 표본이며 whole count라고 표시하지 않는다.
대량 초기 정리와 새 partial index 설치는 서로 별도 gate이며 agent가 운영DDL을 실행하지 않는다.

## 전체 보존 경로와 미래 예보 보호

발표가 오래됐더라도 미래 대상시각의 KMA 단기예보는 전역 known_at 보존기간으로
삭제하지 않는다. 다음 source 정본이 동일한4개 discriminator와 valid_at 경계를 공유한다.

| 경로 | source | 보호 |
| --- | --- | --- |
| 수집·재수집 | repository._ingest_batch_session / retention.expired_short_forecast | 지난6시간 밖의 새 fact·replay projection을 제외, source/raw 보존 |
| 단기예보 정리 | repository.purge_expired_short_forecast / cli / Dagster 전용 job | 엄격 valid_at < cutoff, 정확 복합키 참조 순서 |
| 전역 일별 정리 | repository.purge_expired_history / _detach_expired / _drop_detached | 최근·미래 단기예보 pointer 보존, fact가 남은 partition DDL 보류 |
| 수동 DEFAULT 부분 정리 | default_partition.purge_expired_rows | 같은 미래 fact와 projection을 삭제 대상에서 제외 |
| 수동 DEFAULT 전체 교체·잔여 DROP | default_partition.swap_default / drop_old_default | 테이블 잠금 후 보존 fact 존재 검사, 있으면 작업 거절 |

partition 보호 조회는 기존 statement_timeout과5초 중 더 작은 문장 예산을 사용한다.
0(무제한)이면5초로 제한하며 성공 후 기존 설정 원문을 복원하므로 후속 DDL 예산은 바꾸지 않는다.
확정 False일 때만 DDL을 허용한다. timeout·DB 오류·NULL은
삭제 허가로 바꾸지 않는다. 보존된 일별 partition은 partitions_retained_short_forecast로
따로 보고한다. 전체 partition이 더 오래 남으므로 그 안의 다른 product가 함께 남을 수 있지만,
다른 product의 기존 행 삭제 predicate 자체를 늘리거나 줄이지 않는다.
이미 detached된 과거 데이터의 projection 재구축은 이 변경의 자동 복구 범위가 아니다.

10분 schedule은 queue 대기·worker 중단·처리량 부족으로 늦어질 수 있어 물리 삭제의
정확한 wall-clock SLA를 보장하지 않는다. 새 만료 fact 유입 차단과 유한 정리 작업을 함께
제공하며 초기 backlog는 root가 disk/WAL·계획·배치 결과를 보고 별도 완주해야 한다.


## 늦은 미래 응답의 재입장

원 known_at이 DEFAULT 또는 실제 dated partition에 수용되면 그대로 저장한다.
원 fact가 없고 그 clock이 retention gap으로 수용 불가하면 보존할 단기예보만 실제 재입장
시각을 known_at으로 사용한다. 기존 source의 earliest retained row가 있으면 그 pair가 우선이며,
다음 replay도 같은 pair를 재사용한다. source.fetched_at에 원 canonical clock을 남기고
collected_at/issued_at/valid_at/payload/source_record_key는 바꾸지 않는다.
배치당 기존 catalog 조회를 공유하고, 원 source를 이미 가진 재입장 후보만 value_id별 최초 row를
최대1000개씩 조회한다. row별 query, DEFAULT CHECK 변경, 과거 partition ingest DDL은 없다.
현재 재입장 시각 자체도 수용할 partition이 없으면 schema 오류를 숨기지 않고 실패한다.
새 예외는 [ADR-072](adr/072-weather-bitemporal.md)와 data-model에 명시했다.

## 검증 범위와 실패 이력

최초 f64 전체 시험588PASS/2FAIL은 보존했고 현재시각 기반 fixture 두 건과 미래 재입장 예외를
보강한6fa는 전체599PASS/49warnings(652.21초), Ruff 통과했다. frontend196/type 검증은
수정 없는101 기준이다. 이 수치를 뒤에 추가한 timeout/index 소스의 최종 전체 결과로 합산하지 않는다.

별도 소유 PostgreSQL의 TEMP 표에서 원6fa 보호 조회가2초 예산을5초로 늘리는 RED를 확인했다.
새 구현은0/60초/2초/50ms의 원문 설정 복원을 확인했다. 단기예보1000행을 포함한 합성1만행
TEMP partition에서는 기존 Sort→새 Index Scan 계열/no Sort, 동일25개 복합키, 새 leaf 상속,
downgrade/reapply의 동일 결과를 확인했다. 이 로컬 소표본은 운영 실행시간·CPU/WAL 절감 증거가 아니다.
최종 통합 전체 시험과 두 독립 source 리뷰, root 운영 설치·초기 정리는 별도 gate다.

## 운영 사전 실측과 JIT

인덱스 적용 전 실제 정리 후보1000개 쿼리는2.92초였고,
domain 전체 건수 조회는5초 제한으로 취소됐다. 이는 인덱스 설치 전
읽기 전용 사전값이며 정리 완료나 설치 후 성능 수치가 아니다.

동일 쿼리·고정 cutoff를 PostgreSQL16.10에서 JIT on/off/on/off로 비교하면
실행1541/747/1735/778ms였다. JIT 컴파일 자체는 약792/845ms였다.
이 workload에서는 JIT 추가 사용보다 반복 스캔과 정렬을 줄이는 정적
부분 인덱스를 우선한다. 전역 JIT 설정을 이 기능이 변경하지 않으며,
실제 인덱스 설치 후 비용·JIT 사용 여부·처리시간을 다시 확인한다.
JIT는 장시간 CPU 집약 쿼리에 유리할 수 있고 짧은 쿼리에서는 컴파일
오버헤드가 더 클 수 있다.
[PostgreSQL16 공식 설명](https://www.postgresql.org/docs/16/jit-decision.html)

독립 리뷰의 최초 반례·수정·시험 이력은
[closure](reviews/2026-10-16-weather-shortterm-retention-closure.md)에 보관한다.

## 2026-10-27 상시 실패와 이어 읽기

### 관측(운영 odroid, 읽기 전용 2026-10-26 22:2xZ)

- 공용 Dagster: `weather_short_forecast_retention_job`의 마지막 성공은 10-20 22:50Z이고 그 뒤 모든 run이
  FAILURE다(최근7일 FAILURE835·SUCCESS63, 성공은 모두 10-19 22:30Z~10-20 22:50Z).
  실패 run은 batch9~17개×1000행을 지우고도 `budget_exhausted`라 Failure였다.
  21:40Z·22:00Z run은 첫 후보 SELECT가 5초 `statement_timeout`(QueryCanceled)으로 죽었다.
- 일별 partition은 7~9M행(heap4~5GB·index3.5~4.6GB)이고 단기예보 부분 인덱스 leaf는 1~2.9M항목이다.
  삭제가 일어난 1020/1021/1022 leaf의 dead는 1,276,815/800,872/282,038, 마지막 autovacuum은 각
  10-20/21/22였다. 기본 scale factor0.2에서는 약1.4M dead 전에는 vacuum되지 않는다.
- 전체 partition 머리부터 첫 만료 행 1개를 찾는 데 cold 15.3초·buffer read34,708이 걸렸다(1020 20,986,
  1021 13,704). dead가 없는 1023 leaf에서 1000행 선택은 22ms·21 buffer다.
- 같은 1000키의 PK 탐색은 fact 1,525 page·2.5초, pointer 948 page·2.4초였다. 삭제 계획은 둘 다
  VALUES×PK Nested Loop이다. xmin을 붙든 긴 transaction·slot·prepared xact는 없었다.

### 원인

1. 매 batch가 부분 인덱스 머리부터 후보 SELECT와 남은행 EXISTS를 두 번 다시 읽었다. 지운 행의
   인덱스 항목은 VACUUM 전까지 머리에 남으므로 batch마다 수백만 dead 항목을 지났다(batch5~8초).
2. 진척이 있어도 완료가 아니면 Failure여서 실행마다 실패 이력·경보를 남겼다.
3. 실행당 약1만 행은 유입(약2.5~2.9M행/일, 10분당1.7~2만)보다 적어 backlog가 줄지 않았다.

### 현재 동작

- 후보는 부분 인덱스 순서 `(valid_at, known_at, value_id)`로 고른다. 다음 batch는 앞 batch의 마지막
  키 뒤에서(`ROW(...) > ROW(...)`가 Index Cond), 다음 실행은 앞 materialization metadata의
  `resume_valid_at/resume_known_at/resume_value_id` 위치에서 시작한다. 위치가 없을 때만 첫 선택이
  머리를 지나며 그 한 문장에 남은 예산 안 최대45초를 준다. 나머지 문장은 5초·lock1초 그대로다.
- 위치는 검사한 뒤에만 쓴다(`resume_position`). 정책(`resume_policy` = 6시간+predicate 해시)이나
  DB 이름(`resume_database`)이 다르면, 위치가 경계보다 앞서 있으면(시계 역행), 마지막 머리 훑기
  (`head_scan_at`)가 24시간 넘었으면 머리부터 다시 시작한다. 정상 수집은 경계보다 오래된 단기예보를
  저장하지 않지만 복원·늦은 commit·시계 역행 등으로 위치 앞에 만료 행이 생길 수 있어, 하루 한 번의
  머리 훑기가 그런 행을 영구히 놓치지 않게 한다. 주기 훑기의 첫 선택이 VACUUM 전 죽은 항목 때문에
  45초 안에 끝나지 않으면(QueryCanceled) 같은 실행이 저장 위치에서 이어 정리하고
  `head_sweep_timed_out=true`를 남긴다(훑기는 다음 주기에 다시 시도). 행 잠금은 SKIP LOCKED 대신
  `lock_timeout` 안에서 기다리므로 한 실행이 건너뛴 잠긴 행이 위치 앞에 남지는 않는다.
- pointer는 기존처럼 정확 pair로 먼저 지운다. fact는 같은 transaction에서 FOR UPDATE로 잠근 행을
  `tableoid`+`ctid`로 partition마다 지운다(known_at 범위로 pruning). RETURNING 키 집합이 고른 키와
  같아야 commit한다. PK 재탐색이 없어진다.
- 남은행 확인은 덜 찬 batch면 생략(위치 뒤를 모두 고름), 찬 batch면 위치 뒤 EXISTS만 쓴다.
- 문장 timeout·잠금 timeout·deadlock(57014/55P03/40P01)은 그 batch만 rollback하고 `deferred`로
  돌린다. 앞 batch commit과 위치는 유효하다. 다른 SQL 오류는 그대로 실패다.
- 판정: 지운 행이 있거나 `complete_snapshot`이면 SUCCESS다. backlog가 남으면 warning과
  `backlog_lag_seconds`를 남긴다. 이 값은 경계 − 마지막으로 지운 키의 대상시각이고 남은 가장 오래된
  행은 그 뒤에 있으므로 실제 지연의 **상한**이다. `lag_stall_runs`는 이 상한이 24시간을 넘는 채 앞
  materialization보다 줄지 않은 연속 실행 수다(줄면, 또는 24시간 이하면 0). Failure는 진척0일 때와
  `lag_stall_runs`가 7(약70분) 이상일 때만 남긴다. 뒤처졌어도 줄고 있으면(따라잡는 중) SUCCESS다.
  valid_at은 정시 단위라 지연 상한은 같은 정시 안에서 실행마다 600초씩 오르다가 위치가 다음 정시로
  넘어갈 때 한꺼번에 준다. 처리량이 유입 이상이면 6실행(1시간) 안에 정시를 넘어 횟수가 0으로 돌아가므로
  7회 연속은 1시간 넘게 정시 하나도 넘지 못한 상태(처리량이 유입의 약0.85배 미만)다.
  정책·DB 변경·시계 역행으로 위치를 버린 실행은 앞 지연·횟수를 이어 쓰지 않고 새로 센다.
  후자의 Failure도 실패 전에 같은 metadata로 materialization을 먼저 기록해 다음 실행이 위치와 횟수를
  승계한다. dry-run은 진척으로 세지 않는다.
- 실행당 예산은 설정 `KOR_TRAVEL_WEATHER_SHORT_FORECAST_RETENTION_BUDGET_SECONDS`(기본150초, 상한180초)다.
  운영 실측 batch 약3초/1000행이면 실행당 약5만 행으로 유입(10분당 약1.7~2만)의 약2.5배다. 마지막 batch와
  run 기동을 더해도 job의 `dagster/max_runtime` 300초 안에 끝나는 상한이다. odroid 부하가 높으면 낮춘다.
  weather 동시 run 상한(6) 중 한 자리를 실행마다 약2.5~3.5분 쓴다.
- 6시간 경계·미래 보존·advisory lock·100batch/1000행·10분 주기는 바꾸지 않았다.
  CLI `ktwctl purge-short-forecast`(기본 예산60초)는 위치를 저장하지 않고 머리부터 실행 안에서만 이어
  읽는다(위치를 의심할 때의 전체 재확인 경로).

### 배포 후 확인

1. `weather_short_forecast_retention_job`을 한 번 실행하거나 다음 10분 tick을 기다린다.
2. 첫 run은 위치가 없어 머리를 한 번 지난다(최대45초). SUCCESS·`facts_deleted>0`·`resume_*`·
   `head_scan_at`·`resumed=false`·`budget_seconds=150`을 확인한다. backlog가 남으면 `budget_exhausted`·
   `remaining=true`이고 배포 직후에는 10-20부터 쌓인 backlog 때문에 `backlog_lag_seconds`가 24시간을 넘는다.
3. 다음 run부터 `resumed=true`이다. `backlog_lag_seconds`는 같은 정시 안에서 run마다 600초씩 오르고 정시를
   넘을 때 크게 준다. 시간 단위로 보아 줄면 따라잡는 중이며 `lag_stall_runs`는 0~5 사이를 오간다.
   BATCH_COMMITTED observation의 `elapsed_seconds` 증가 폭이 batch 시간이다. 하루 한 번은
   `resume_discarded=periodic_head_sweep`인 머리 훑기 run이다(`head_sweep_timed_out=true`면 위치에서 이어 정리).
4. Failure는 두 경우뿐이다. 진척0(`facts_deleted=0`, `error_class`로 잠금/timeout 구분)과, 24시간 넘게
   뒤처진 채 7회(약70분) 연속 줄지 않음(`lag_stall_runs>=7`, 처리량 부족 — 예산을 올리거나 부하를 확인).

### 남은 한계

- 1000행마다 pointer FK(RESTRICT) 검사가 pointer PK를 무작위로 찾는 비용(운영 cold 약2.4초)은 남는다.
  처리량 추정(약5만 행/실행)은 이 비용 기준이며 운영 부하에 따라 달라진다. 실제 따라잡는 속도는 배포 뒤
  `backlog_lag_seconds` 추이로 확인한다.
- 위치 앞에 늦게 들어온 만료 행은 다음 하루 머리 훑기까지(최대 약24시간) 남을 수 있다. 머리 훑기가 죽은
  항목 때문에 계속 시간 초과면 그 행은 7일 partition DROP까지 남는다. CLI 전체 실행은 언제든 머리부터 정리한다.
- 지연 상한 gauge는 아직 내보내지 않는다. 짧은 run worker의 multiprocess live gauge는 run이 끝나면
  사라지고, API scrape 시점 계산은 머리 탐색 비용이 든다. 현재 알림 신호는 위 두 Failure다.
