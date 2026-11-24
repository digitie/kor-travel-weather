# 쓰기 경로 상한: 이력 정리 batch·수집 문장 상한·payload 미적재·migration lock 기본값

2026-11-24 [Map 호출 검토](reports/2026-11-24-map-call-performance-review.md) §3.2의 L4~L7을
반영한다. L8(freeze RR 분리)은 A4와 같은 일이라 이 묶음에 넣지 않았다.

| # | 위치 | 전 | 후 |
| --- | --- | --- | --- |
| L4 | `purge_expired_history` 포인터·원천 정리 | 상한 없는 단일 DELETE 두 문장 | 기본키 keyset batch 1,000행, batch마다 commit·문장 10초·lock 1초 |
| L5 | 수집 writer transaction | lock 대기 상한만 | 문장 300초·문장 사이 유휴 60초를 그 transaction에만 |
| L6 | 재수집 판정 조회 | 원천 원문 payload(최대 8MiB)까지 읽어 identity map에 보관 | `defer(SourceRecordRow.payload)` |
| L7 | `alembic/env.py` | migration 기본 `lock_timeout` 없음 | 연결 시작 옵션 `-c lock_timeout=5s`, migration 자신의 설정이 우선 |

## L4 야간 이력 정리의 batch

`purge_expired_history`의 두 DELETE를 같은 틀(`_run_history_purge_batches`)로 나눈다.

- **현재값 포인터**(`pointer_purge_sql`): `known_at < cutoff`인 포인터를 `value_id`(기본키) 순서로
  1,000개 후보로 고르고, 그중 보존할 단기예보(최근 6시간·미래 `valid_at`)를 가리키지 않는 것만 지운다.
- **원천 기록**(`source_purge_sql`): `fetched_at < cutoff`인 원천을 `source_record_key`(기본키) 순서로
  1,000개 후보로 고르고, 기존 `PURGE_SOURCE_RECORDS_SQL`(fact·run 연결·특보 인용 세 `NOT EXISTS`)과
  떼어 두고 아직 못 지운 표의 인용 조건을 만족하는 것만 지운다. 그 표 목록은 batch마다 advisory lock
  아래에서 다시 읽는다.

한 문장이 (후보 수, 마지막 후보 키, 지운 수)를 돌려준다. 다음 batch는 마지막 후보 키 뒤에서 시작하므로
지우지 않은 후보(보존·인용 중)도 다시 읽지 않는다. 후보 수가 1,000보다 적으면 끝이다. DELETE는 후보
키 배열(`= ANY`)을 기본키 조건으로 쓸 수 있게 썼다(`IN (SELECT ...)` semi-join은 hash join으로 표를
훑을 수 있다). 운영 규모의 실제 계획은 확인하지 않았다. LIMIT은 상수로 넣는다(psycopg가 같은 문장을
여러 번 실행한 뒤 prepare하면 generic plan이 매개변수 LIMIT을 행의 10%로 추정해 정렬로 갈 수 있다).

**상한과 순서.** batch마다 새 transaction에서 `weather_history_purge` advisory lock을 먼저 기다려
잡고(예전과 같이 상한 없이), 그 뒤에 `SET LOCAL statement_timeout = '10000ms'`와
`lock_timeout = '1000ms'`를 건다. lock 상한을 먼저 걸면 운영자 DEFAULT 교체가 쥔 advisory lock
대기까지 1초에 끊긴다. 1,000행은 단기예보 정리 batch와 같은 크기다. 그쪽 운영 실측은 약 3초/1,000행
(`docs/shortterm-retention.md`)이라 10초 안에 든다. 이 두 DELETE의 운영 batch 시간은 재지 않았다.

**keyset을 기본키로 고른 이유.** 두 표 모두 `known_at`·`fetched_at` 단독 인덱스가 없다. 운영 보존은
7일이라 평소 밤의 만료 행은 표의 약 1/8로 추정하고(운영 분포는 재지 않았다), 그러면 기본키 순서로
1,000개를 찾는 데 약 8,000개 항목과 그 heap을 읽는다. 새 인덱스는 큰 표의 `CREATE INDEX CONCURRENTLY` migration이 필요해 이 범위에 넣지
않았다. 밀린 뒤 첫 실행은 만료 행 비율이 높아 batch당 읽는 양이 오히려 준다.

**재시도와 연기.** lock/문장 상한·deadlock(57014·55P03·40P01)에 진 batch는 rollback되고 같은
위치에서 2·4·8초 뒤 다시 시도한다(모두 4회). 그래도 지면 앞 batch의 commit은 그대로 두고 그 단계를
멈춘다.

- 포인터: `pointers_deferred`. 이번 실행은 **만료 일자를 떼지 않는다.** 남은 포인터가 가리키는 fact를
  떼어 내면 FK 검증이 그 포인터를 상한 없는 DELETE(`delete_dangling_projection_rows`)로 지우게 되기
  때문이다. 그 날들은 할 일을 못 한 것으로 표시되고(`partitions_deferred`) 다음 밤에도 못 하면
  overdue → `partial`/`failed`다. 이미 떼어 둔 표의 DROP은 계속한다.
- 원천: `sources_deferred`. 남은 원천은 다음 실행이 지운다.
- 둘 다 실행 상태는 `deferred`다(Dagster asset metadata에 두 값이 나간다).

**예전과 달라진 점.** 예전에는 한 DELETE가 한 transaction이라 원자적이었다. 이제 batch 사이에 다른
transaction이 끼어든다: 같은 advisory lock을 try로 잡는 단기예보 정리 batch, 운영자 DEFAULT 교체,
수집. 지우는 조건은 각 batch의 DELETE가 다시 평가하므로 그 사이 수집 CAS가 더 새 판으로 옮긴 포인터는
지우지 않는다. 특보 이벤트 정리(`purge_alert_events`)는 바꾸지 않았다(표가 작고 31일 보존). 원천
정리보다 먼저 자기 transaction에서 끝낸다.

## L5 수집 transaction의 문장·유휴 상한

`_set_ingest_write_limits`가 원천·fact를 게시하는 transaction(`ingest_batch`·`ingest_skip_locked`·
`publish_and_finish`)에 `set_config(..., true)`로 두 상한을 건다. transaction이 끝나면 연결 설정으로
돌아가고, 연결·role에 이미 더 엄격한 값이 있으면 그 값을 둔다.

- **문장 하나 300초**(`INGEST_STATEMENT_TIMEOUT_MS`). 보고서 권고는 30초였다. 그러나 odroid의 KMA
  단기예보 게시는 run당 384~441천 fact에 5,539~7,881초(weather PR #286의 14일 운영 Dagster storage
  읽기 전용 조회, 그 PR의 `docs/kma-short-forecast-runtime.md`)라 2,500 fact chunk 하나가 평균 약
  45초(추산)이고, n150 디스크 대기 때는 chunk 하나가 수 분이었다
  (`docs/architecture/dagster-boundary.md`). 30초는 디스크가 밀릴 때 정상 문장을 거짓 실패시켜 몇
  시간짜리 run 전체를 버린다. 300초는 그 최악 chunk 평균(약 150초)의 두 배이고, 계획 회귀로 fact 표를
  훑는 문장이 location lock을 몇 시간 쥐는 일(2026-10-02)을 5분으로 끊는다.
- **문장 사이 유휴 60초**(`INGEST_IDLE_IN_TRANSACTION_TIMEOUT_MS`). transaction 안의 Python 일(원천
  정규화 묶음, export chunk 준비)은 보통 1초 미만이고 provider HTTP와 재시도 휴지는 transaction
  밖이다. 프로세스가 멈추거나 연결이 끊긴 채 lock을 쥔 writer를 서버가 60초 뒤 끊는다.
- 넘기면 재시도하지 않는다(57014·25P03은 lock 경합이 아니다). 그 chunk가 실패하고 앞 chunk는
  남는다(기존 부분 게시 의미 그대로).
- **writer 경로에만.** `_set_ingest_lock_timeout`만 쓰는 export 읽기·lifecycle transaction
  (`ExportLifecycle._transaction`, export heads의 문장 5초 transaction)에는 걸지 않는다. 특보 게시
  (`publish_alert_events`)는 이미 문장 30초를 스스로 걸고 있고 다른 PR(#285)이 같은 함수를 바꾸고
  있어 이번에는 유휴 상한을 넣지 않았다.
- **role 수준 설정은 권하지 않는다.** 보고서는 writer role에 `idle_in_transaction_session_timeout`을
  권했지만 지금 API·Dagster·migration이 같은 role이다. role에 걸면 export freeze 같은 읽기
  transaction도 끊긴다. 전용 writer role을 나누기 전까지는 위 transaction 단위 설정으로 대신한다.

## L6 재수집 판정의 payload 미적재

`_record_source_session`의 기본키 조회(`session.get`)와 identity 조회, `_resolve_fact_session`의
기본키 조회에 `defer(SourceRecordRow.payload)`를 준다. 판정은 identity 열(`provider`·`dataset_key`·
`source_entity_type`·`source_entity_id`·`raw_payload_hash`)과 `fetched_at`만 쓴다. 배치 원천
prefetch는 이미 payload를 빼고 있었다. 새 원천의 INSERT는 그대로 payload를 쓴다.

## L7 migration 기본 lock_timeout

`alembic/env.py`의 engine이 `connect_args`(`kortravelweather.migration_limits.migration_connect_args`)로
`-c lock_timeout=5s`를 준다. 시작 옵션은 그 연결의 기본값(reset 값)이 되므로 migration 자신의 설정이
늘 우선한다.

- `SET LOCAL lock_timeout`·`set_config(..., true)`는 그 transaction에서 이긴다.
- `SET lock_timeout`(0012의 `SET lock_timeout = 0` 포함)은 그 연결의 남은 migration에 간다. 새 DB에서
  0012부터 한 번에 올리면 0013 이후는 상한 없이 돈다(경합이 없는 새 DB라 그대로 둔다).
- `SET LOCAL lock_timeout TO DEFAULT`(0017)·`RESET`은 서버 기본(0)이 아니라 이 5초로 돌아간다. 0017의
  주석이 말하는 "세션 자신의 값"이 바로 이것이다.
- 0022·0032~0043의 "현재 값과 상한 중 더 엄격한 값" 규칙은 5초가 더 엄격하면 5초를 쓴다. 이 규칙의
  상한이 5초보다 큰 것은 0022(30초)뿐이고 운영 DB에는 이미 적용됐다.
- URL에 `options`가 있으면 그 뒤에 둔다. 같은 설정을 여러 번 주면 뒤의 값이 이기므로 운영자가 URL로 준
  `lock_timeout`이 우선한다. libpq `PGOPTIONS` 환경변수는 이 `options` 인자에 가려진다.

**재시도 헬퍼는 표준화하지 않았다.** 지금 있는 것은 0012의 짧은 `lock_timeout`·30회 재시도,
`default_partition._with_lock_retries`(운영자 DEFAULT 교체), `partitions.lock_for_partition_ddl`
(0017·야간 유지보수와 같은 lock 순서)다. 대상 표·lock 순서·실패 뒤 처리(멱등 재실행인지, 버전 미기록으로
다음 배포에 맡기는지)가 서로 달라 하나로 묶으면 각 migration의 근거를 지운다. 새 migration은 다음을 따른다.

1. 기본 5초로 충분하면 아무것도 하지 않는다. 넘기면 migration이 실패하고 배포가 멈춘다.
2. 더 기다려야 하면(예: `CREATE INDEX CONCURRENTLY`가 긴 수집 transaction을 기다리는 단계) 그
   transaction이나 autocommit 블록에서 명시적으로 늘리고, 끝나면 `RESET`이나 이전 값으로 되돌린다.
3. 짧게 물러나 다시 시도해야 하는 DDL은 0012처럼 멱등 문장(`IF NOT EXISTS`)과 짧은 상한·유한 횟수로 쓴다.

## 검증

로컬(Windows 개발 PC, WSL docker `postgres:16` 전용 컨테이너)만 확인했다. 운영 batch 시간·수집 문장
시간 분포는 재지 않았다.

- `tests/test_history_purge_batches.py`: batch마다 advisory lock → 문장 10초 → lock 1초 순서와 별도
  commit, `:after` 이어 읽기, 지우지 않는 보존 후보가 위치를 멈추지 않음, 잠긴 포인터에서 연기되면
  앞 batch는 남고 만료 일자는 붙은 채 다음 실행이 지움, 원천 batch의 57014 연기와 앞 commit 보존,
  다른 SQL 오류는 그대로 실패, 후보 조회가 기본키 순서(정렬 없음)로 가능함.
- `tests/test_ingest_write_limits.py`: 세 writer 경로의 상한(`5min`·`1min`)과 transaction 뒤 원복,
  더 엄격한 연결 설정 유지, export 쪽 `_set_ingest_lock_timeout`은 상한 없음, 유휴 writer가 25P03으로
  끊겨 commit되지 않음, 느린 문장이 57014로 한 번만 실패.
- `tests/test_replay_source_payload.py`: 같은 키 재수집·새 키 같은 원문(identity 조회)·원천 키 없는
  fact의 대체 원천 재조회에서 `weather_source_records` SELECT에 payload 열이 없음. `defer`를 빼면
  실패함을 로컬에서 확인했다.
- `tests/test_migration_lock_timeout.py`: 연결 옵션, `SET LOCAL`·`TO DEFAULT`·`RESET` 우선순위,
  `alembic upgrade`가 실제로 여는 연결의 `lock_timeout`이 5초.
- `packages/kor-travel-weather-dagster/tests/test_retention_status.py`: 두 연기 값이 `deferred`·metadata로.
