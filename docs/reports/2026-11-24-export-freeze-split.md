# current export freeze 트랜잭션 분리 (호출 검토 A4·L8)

근거: [Map 호출 속도 개선 검토](2026-11-24-map-call-performance-review.md)의 A4·L8.
대상: `src/kortravelweather/export_snapshots.py`의 `FrozenCurrentExports._freeze`
(current 첫 페이지와 사전 구성 builder가 함께 쓴다).

## 바꾼 것

예전 `_freeze`는 REPEATABLE READ 하나에서 선택(catalog·승인 선택·세대 ref·주소·상태 판정)과
발급(같은 digest handle 재사용 조회·snapshot INSERT·ref INSERT·파일 존재 확인·commit)을 모두 했다.
문장마다 5초 상한만 있고 트랜잭션 전체 상한은 없었다.

지금은 두 트랜잭션이다.

| 단계 | 격리 | 하는 일 | 상한 |
| --- | --- | --- | --- |
| 선택 RR (`_freeze_plan`) | REPEATABLE READ, `READ ONLY` | catalog·승인 선택·세대 ref·주소 조회, manifest·digest 계산, 상태 판정(#290), 사전 구성 proof | 문장 5초 |
| 발급 TX (`_issue_frozen`) | READ COMMITTED | 같은 digest의 최신 handle 재사용 조회, snapshot·ref INSERT, (immutable 모드) guard 안 파일 확인·commit | 문장 5초, `lock_timeout` 1초 |

두 단계 사이에서 요청자 연결이 끊겼는지 확인한다(`checkpoint("freeze")`). 끊겼으면 handle을 쓰지 않는다.

## RR 일관성을 깨지 않는 이유

- manifest·digest·상태 판정·사전 구성 proof는 모두 선택 RR의 한 snapshot에서 나온다.
  발급 TX는 catalog·승인·세대·주소를 다시 읽지 않고 선택 RR이 계산한 값만 쓴다.
- 같은 digest면 manifest 전체(승인 head vector·artifact ref·위치·selection)가 같다. 발급 TX가 찾은
  handle이 선택 RR 뒤에 다른 요청이 발급한 것이어도 내용은 같으므로 재검증하지 않는다.
- 재사용 판정의 "selection의 가장 최근 발급"은 선택 RR 기준이다. 선택 RR이 같은 digest 최신 handle과
  그것이 selection 최신이었는지를 plan에 남긴다(isible_issue_order·eusable_snapshot_id).
  발급 TX(READ COMMITTED)는 다음 순서로 고른다.
  1. 선택 RR 뒤에 이미 commit된 같은 digest handle이 지금도 selection 최신이면 그것을 재사용한다.
  2. 아니면 선택 RR이 최신으로 본 같은 digest handle을 (아직 유효하면) 재사용한다. 선택 RR 뒤에
     다른 digest handle이 발급됐어도 예전 단일 RR처럼 재사용하므로, 오래된 plan의 handle을 그 뒤
     순서로 INSERT해 selection 최신 handle(_previous_success·사전 구성 재사용의 기준)이 오래된
     내용으로 바뀌지 않는다.
  3. 둘 다 아니면 새로 발급한다.

  동시에 열린 발급 TX끼리는 서로의 미commit INSERT를 보지 못하고 manifest_digest에 unique 제약이
  없으므로, 같은 내용의 handle이 둘 생길 수 있는 것은 예전과 같다.
- 상태 판정(#290)은 선택 RR 안에서 계산하고 발급된 handle의 `(snapshot_id, digest)`에 묶는다.
  판정 수명은 예전처럼 RR 시작 전 시각에서 센다(60초 승인 수명을 늘리지 않는다).
- 선택 RR 뒤에 참조 세대가 지워지면(GC와 겹친 드문 경합) ref INSERT의 FK 검사가 23503을 낸다.
  발급 TX를 되돌리고 `AtomicPinMissing`으로 `ensure_snapshot_files`가 새 head로 다시 고정한다
  (최대 3회, 그 뒤에는 일시 경합 503).
- 선택 RR은 `SET TRANSACTION READ ONLY`라 그 안에서 쓰는 코드가 생기면 DB가 25006으로 막는다.

## 기대 효과와 한계

- xmin을 고정하는 RR은 읽기 동안만 열린다. INSERT·FK 검사·파일 guard 대기·commit은 RR 밖이다.
- 발급 TX의 행 잠금 대기는 `lock_timeout` 1초에서 55P03(일시 경합 503)으로 끝나고 5초 문장
  상한(57014)까지 기다리지 않는다.
- 선택 RR 자체(승인 선택 조회)는 그대로 한 RR이다. 같은 snapshot에서 읽어야 하는 입력이라 더
  나누지 않았다. 선택 RR의 긴 조회가 문장 5초를 넘는 경우(57014)는 이 변경으로 줄지 않는다.
  그 비용은 사전 구성(A3, #301)과 승인 선택 캐시가 줄인다.
- PostgreSQL 16에는 트랜잭션 전체 상한(`transaction_timeout`, 17+)이 없어 선택 RR 전체 상한은
  넣지 않았다.
- freeze 한 번이 pool 연결을 두 번 빌린다(선택 RR을 반납하고 발급 TX에서 다시 빌린다). engine이
  `pool_pre_ping=True`라 checkout마다 `SELECT 1` 왕복이 하나 더 들고, pool이 찬 부하에서는 선택 RR을
  끝낸 요청이 발급 TX 연결을 다시 기다리다(`pool_timeout` 15초) PoolTimeoutError(503)로 계산 결과를
  버릴 수 있다. 측정하지 않았다. 같은 DBAPI 연결에서 RR commit 뒤 READ COMMITTED TX를 이어 여는
  방식(xmin 해제 효과는 같다)은 필요해지면 검토한다.
- 운영 실측은 하지 않았다. 효과 수치는 배포 뒤 freeze 단계 시간·57014/55P03 빈도로 확인한다.

## 하지 않은 것: freeze 예산 초과 시 이전 승인을 stale로 반환

검토 문서 A4는 "freeze 예산 초과 시 503 대신 `_previous_success`를 stale로 반환하는 경로 확대"를
소유자 결정 항목으로 두었다. 이번 변경에는 넣지 않았다.

- 기존 계약은 DB 경합(57014·55P03)을 `일시 경합 503/Retry-After`로 알리고, `stale`은 원천 승인·
  수집 상태가 확인되지 않았다는 뜻으로 쓴다(`docs/reports/2026-11-07-read-next.md`,
  `export_selection_cache.py`의 "만료/갱신 실패 때 이전 정상 상태로 연장하지 않는다").
  서버 경합을 `stale` 200으로 바꾸면 Map이 원천 문제와 Weather 과부하를 구별할 수 없다.
- `_previous_success`로 돌려도 같은 요청이 상태 판정용 RR(승인 선택 재조회)을 다시 열어야 해서,
  같은 경합에서는 다시 57014가 될 가능성이 높다. 만료된 handle이면 복제 INSERT도 더한다.
- Map 쪽(kor-travel-map #1542, 열린 PR)은 weather 503을 상류 미준비 신호로 보고 건너뛰며 기존
  세대를 지키도록 바뀌는 중이다. 같은 상황에 stale 200을 주면 Map이 오래된 승인 값을 다시 적재할 수 있다.
  응답 의미를 바꾸려면 Map 계약과 함께 소유자가 정해야 한다.

## 검증

`tests/test_export_freeze_split.py`가 다음을 확인한다.

- 선택 RR은 `READ ONLY`·REPEATABLE READ이고 INSERT가 없다. 발급 TX는 READ COMMITTED·
  `lock_timeout`이며 snapshot 표만 읽는다.
- 선택 RR 안에서 쓰기를 시도하면 25006이다.
- 선택 RR 뒤에 commit된 주소 변경은 저장된 manifest·digest에 섞이지 않고, 상태 판정은 발급 handle에
  묶인다. 다음 freeze는 새 값과 다른 digest를 본다.
- 선택 RR 동안 다른 요청이 발급한 같은 digest handle을 재사용한다(handle 1개).
- 선택 RR 뒤에 다른 digest handle이 발급돼도 선택 RR이 최신으로 본 handle을 재사용하고 행을 늘리지
  않는다. selection 최신 handle은 더 새 내용으로 남고 다음 같은 내용 요청이 그것을 재사용한다.
- 선택 RR 뒤 같은 digest handle 다음에 다른 digest handle이 또 발급됐고 선택 RR의 재사용 후보가
  없으면 새로 발급한다(예전 단일 RR과 같은 판정).
- 세대 행 잠금 대기는 4초 안에 55P03으로 끝나고 handle을 남기지 않는다.
- 지워진 세대를 참조하면 `AtomicPinMissing`으로 되돌리고 handle을 남기지 않는다.
