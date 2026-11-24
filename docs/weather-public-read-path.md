# 공개 읽기 경로: worker 직렬화·연결 하나·lock 상한·마커 입장 상한

`docs/reports/2026-11-24-map-call-performance-review.md` §5의 B3·B9·B10·B11과 §3.2 끝의 공개 읽기
`lock_timeout` 권고를 구현한 계약이다. 대상은 공개 조회 `/v1/weather/*`(regions, locations,
locations/{id}, latest, forecast, nearby, resolve, markers, alert-events)다. 운영에서 측정하지
않았다. 아래 숫자는 설정값이고, 시험의 횟수(연결 수·설정 문장 수)는 로컬 PostgreSQL에서 센 구조적
값이다.

## B3. 읽기부터 bytes까지 worker thread 하나에서

예전 handler는 읽기 함수마다 `run_in_threadpool`로 thread를 오갔고, 응답 변환(`model_dump`)은
event loop에서 했다. async handler이므로 FastAPI도 그 dict를 `response_model`로 다시 검증하고
직렬화(FastAPI 0.165는 pydantic `dump_json`)하는 일을 event loop에서 했다. 마커 60곳(최대 4,800행)
같은 큰 응답이면 그동안 같은 프로세스의 다른 요청·관리 proxy·`/health`가 기다렸다.

지금은 handler가 `kortravelweather_api.public_response`로 다음을 worker 함수 하나에 넘긴다.

1. DB 읽기(공유 읽기 묶음 안, 아래 B9)
2. 연결을 돌려준 뒤 typed DTO 생성(`WeatherValueOut` 등을 한 번만 검증)
3. `Envelope[...]` 본문을 `TypeAdapter.dump_json`으로 bytes 직렬화
4. 필요하면 gzip(아래)

handler는 `Response(bytes, media_type="application/json")`을 돌려주므로 FastAPI의 응답 재검증·
`jsonable_encoder`는 실행되지 않는다. `response_model`은 OpenAPI 문서에만 쓰이며 그대로 두었다.
CI의 OpenAPI export 비교와 `test_checked_in_openapi_matches_runtime`이 문서 무변경을 확인한다.

본문 동일성: `test_public_read_path.py`는 각 공개 응답을 예전 경로(handler가 JSON 값 dict를 돌려주고
FastAPI가 같은 `response_model`로 검증·직렬화)에 다시 넣은 bytes와 비교한다. 예전 경로도 pydantic
`dump_json`으로 직렬화했으므로 숫자·시각 표기와 필드 순서가 같다. 변경 전후 코드로 같은 fixture를
받아 비교했을 때도 요청 시각에서 계산하는 값(`generated_at`·`duration_ms`, alert-events의
`retained_since`·`queried_since`) 말고는 bytes가 같았다(로컬).

render 단계 시간(직렬화·gzip)은 `ktw_read_stage_duration_seconds{resource="public",stage="render"}`에
남는다. DTO 생성 시간은 들어가지 않는다.

### gzip

- `Accept-Encoding`이 gzip을 `q=0` 없이 허용하고 본문이 1,024 bytes 이상일 때만 worker thread에서
  압축한다(level 4, `mtime=0`). `GZipMiddleware`는 event loop에서 압축하므로 쓰지 않는다.
- 공개 200 응답에는 압축 여부와 관계없이 `Vary: Accept-Encoding`을 붙인다. problem 응답(4xx·5xx)은
  예전 그대로다.
- 관리 웹 BFF(`app/api/weather/[...path]`)의 Node `fetch`는 gzip을 요청하고 풀어서 받는다. BFF는
  `content-encoding`을 전달하지 않으므로 브라우저로 가는 본문은 예전과 같다. 압축은 API→BFF 구간과
  gzip을 요청하는 직접 소비자(PinVi 등)에만 적용된다.

## B9. handler 하나에 연결·transaction 하나

`WeatherRepository.read_scope()`(`kortravelweather.read_scope`) 안에서는 읽기 세션
(`_session_factory()`)과 Core 읽기 연결(`read_connection`)이 처음 연 연결과 transaction을 함께 쓴다.
연결은 처음 필요할 때 열고, 묶음이 끝나면 rollback으로 닫는다. 그래서 handler 하나의 pool
checkout·`pool_pre_ping`·transaction 시작이 한 번이다(시험: latest·forecast·nearby·resolve·markers·
locations 각각 checkout 1회, 예전 코드는 읽기 함수 수만큼).

- READ COMMITTED이므로 문장마다 새 snapshot을 보는 것은 예전과 같다. 바뀐 것은 묶음이 끝날 때까지
  읽은 테이블의 ACCESS SHARE lock을 쥐는 것이다(공개 읽기 마감 15초 안). DTO 변환·직렬화 전에 묶음을
  닫는다.
- 쓰기(`_session_factory.begin()`)는 묶음 안에서도 자기 연결·transaction을 쓴다. 쓰기 commit이 읽기
  묶음을 끝내지 않는다.
- `/regions`의 `region_locations`(REPEATABLE READ 연결)와 읽기 함수가 하나뿐인 handler
  (`/locations/{id}`, `/alert-events`)는 묶음을 쓰지 않는다. 시작 warmup도 단계마다 자기 연결을 쓴다.
- 묶음은 같은 thread에서만 이어진다. B3로 한 요청의 읽기가 한 worker thread에서 돌기 때문에 성립한다.

### 마감 설정은 bind 상수 SQL로 transaction당 한 번

`read_limits.install_public_read_limits`는 예전에 문장마다 `SET LOCAL statement_timeout = '<ms>'`를
따로 보냈다. 문장마다 왕복이 하나 더 들고, 값이 매번 다른 SQL 문자열이 psycopg prepare 후보 LRU를
밀어냈다. 지금은 다음과 같다.

- 상수 SQL `SELECT set_config('statement_timeout', %s, true), set_config('lock_timeout', %s, true)`에
  값을 bind로 넘긴다.
- transaction의 첫 문장 앞에서 한 번 건다(SQLAlchemy `begin` event에서 기록을 지운다).
- 이미 건 값과 이번 문장의 남은 마감(warmup은 문장 상한 5초와 비교한 작은 값)이 50ms(짧은 값은 그
  값의 10%)보다 크게 어긋날 때만 다시 건다. 그래서 한 문장이 요청 마감을 넘겨 실행될 수 있는 시간도
  그 여유 이하다. 빠른 문장이 이어지면 다시 걸지 않고, 느린 문장 뒤에는 다시 걸어도 그 문장에 비해
  왕복 비용이 작다.
- `SET ...` 문장 앞에서는 걸지 않는다. `SELECT set_config`는 snapshot을 잡는 query라 뒤이은
  `SET TRANSACTION ISOLATION LEVEL`을 실패시키기 때문이다(`SET`은 lock을 기다리지 않는다).
- savepoint rollback은 그 안에서 건 설정을 되돌리므로 기록을 지우고 다음 문장에서 다시 건다.

### 공개 읽기 `lock_timeout` 2초

같은 문장에서 `lock_timeout = min(2초, 남은 마감)`을 건다. 야간 파티션 DDL 같은 lock 대기열 뒤에서
마감 15초를 다 쓰지 않고 SQLSTATE 55P03으로 끝나며, 응답은 `503`, `retry-after: 1`,
problem `code=LOCK_TIMEOUT`이다(시험: ACCESS EXCLUSIVE를 쥔 동안 약 2초에 503, 예전 코드는 15초
뒤 `READ_TIMEOUT`). 값은 `read_limits.PUBLIC_LOCK_TIMEOUT_MS`다.

## B10. `/resolve` 후보 선택을 SQL 한 문장으로

예전 handler는 `nearest_locations(radius_km, limit=10_000)`으로 반경 안 활성 위치를 최대 1만 행까지
ORM으로 읽고 Python에서 기준 위치와 후보를 골랐다. 지금은 `WeatherRepository.resolve_locations`가
CTE 한 문장으로 기준 1행과 후보만 읽는다.

- 기준: 반경 안에서 가장 가까운 측정점, 없으면 가장 가까운 위치(거리·`location_id` 순).
  측정점은 `metadata.measurement_point`가 JSON 객체이고 `station_name`이 공백만이 아닌 문자열인
  위치다. API `measurement_point_out`과 같은 판정이며, 공백은 Python `str.strip()`이 지우는 문자
  전부(U+3000 포함)를 정규식으로 옮겼다.
- PostgreSQL `json`의 `->`·`->>`는 문서 전체를 파싱하므로 `metadata`의 어느 문자열에든 JSON
  `\u0000`이 있으면 22P05(unsupported Unicode escape)로 실패한다. 관리 API `LocationCreate`/
  `LocationPatch`의 `metadata`(임의 dict)로 넣을 수 있어서, 반경 안 한 행 때문에 그 지역 `/resolve`
  전체가 500이 되지 않도록 원문 텍스트에 `\u0000`이 있는 행만 `\u0001`로 바꿔 다시 파싱한
  값에서 판정한다(`_nul_safe_json`, CASE라 다른 행은 예전 값 그대로). 두 문자 모두 Python
  `str.isspace()`가 거짓이므로 측정점 판정은 예전 Python 선택과 같다. 응답의 `metadata`는 원래
  값(NUL 포함)이다.
- 후보: 요청 좌표에서 `max(5, 기준 거리 + 5)`km 안(요청 반경 이하)의 측정점이 아닌 다른 위치,
  거리·`location_id` 순. 거리는 모두 요청 좌표 기준이다(예전 Python 선택과 같다).
- 시험은 측정점 경계 사례(공백·전각 공백 이름, NUL이 든 이름, 숫자·null 이름, 문자열·배열
  `measurement_point`, 비활성, 같은 좌표 동률)를 섞은 카탈로그에서 예전 Python 선택과 결과(위치·
  거리)가 같은지 본다.
- 달라지는 경우: 반경 안 활성 위치가 1만 곳을 넘을 때 예전에는 가까운 1만 곳 밖의 측정점·후보를
  보지 못했다. 지금은 반경 전체에서 고른다.
- repository double(`resolve_locations` 없음)은 예전 Python 선택을 그대로 쓴다.

## B11. 마커 입장 대기 3초

`/v1/weather/markers`는 `marker_limiter`(2자리) 대기를 `anyio.fail_after(3초)`로 묶는다. 두 자리가
모두 오래 걸리는 묶음에 잡혀 있으면 마감 15초까지 매달리지 않고 `503`, `retry-after: 1`,
problem `code=MARKER_BUSY`로 끝난다. 거절된 요청은 DB 읽기를 시작하지 않고 자리도 남기지 않는다.
관리 웹 브라우저는 `/v1/weather/*`의 503을 `Retry-After`에 따라 최대 2회 다시 보낸다
(`docs/weather-admin-map-call-resilience.md` B5). 값은 `routers.weather.MARKER_ADMISSION_SECONDS`다.

## 공개 조회 503 code

| code | 원인 |
| --- | --- |
| `READ_TIMEOUT` | 요청 마감(기본 15초) 초과, SQLSTATE 57014 |
| `POOL_BUSY` | pool 대기가 남은 마감을 넘김 |
| `DB_UNAVAILABLE` | DB 연결 단절·재시작(B4) |
| `LOCK_TIMEOUT` | lock 대기 2초 초과, SQLSTATE 55P03 (이번 변경) |
| `MARKER_BUSY` | 마커 자리 대기 3초 초과 (이번 변경) |

모두 `retry-after: 1`이다. problem 스키마는 그대로이고 `code` 값만 늘었다.

## 하지 않은 것

- 마커 compact DTO(B8)는 DTO 변경이라 이 변경에 넣지 않았다. gzip만 공개 조회 전체에 적용했다.
- 시작 warmup은 바꾸지 않았다. 각 단계가 자기 연결을 쓰고, 마감 설정은 위 규칙(transaction당 한 번)을
  따른다.
- 관리 경로(`/v1/admin/*`)와 service export(`/v1/service/*`)는 범위 밖이다. export는 이미 자체 worker
  렌더·gzip을 쓴다.
