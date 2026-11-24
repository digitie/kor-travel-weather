# 관리 지도 호출 무실패화(B1·B2·B5·B4)

`docs/reports/2026-11-24-map-call-performance-review.md` §5·§7-3의 관리 지도(경로 B) 항목을
구현한 계약이다. 목표는 연속 선택·인증 저장소 지연·DB 연결 단절에서 사용자 오류를 일시적인
재시도로 흡수하는 것이다. 운영 수치는 이 변경에서 측정하지 않았다. 아래 숫자는 설정값이다.

## 요청 경로와 각 계층의 상한

```
브라우저 lib/api.ts request()      GET 시도마다 20초, 502/503/504·네트워크·시간 초과 최대 2회 재시도
                                   (504·시간 초과 재시도는 /v1/weather/*만)
  └ Next middleware                durable 세션 확인(GET/HEAD의 active만 10초 캐시, 동시 확인 1회로 합침)
      └ BFF /api/weather/[...path]  모든 GET 헤더 수신 15초 → 504 + retry-after: 1, API retry-after 전달
          └ API /v1/weather/*       DB 마감 15초 → 503 READ_TIMEOUT / POOL_BUSY / DB_UNAVAILABLE
```

브라우저 상한(20초)이 BFF 예산(15초)보다 길어 보통은 BFF의 504와 `retry-after`가 먼저 온다.
BFF 예산은 공개 조회 DB 마감(기본 15초)과 같아서 느린 공개 조회는 API 503 또는 BFF 504로 끝나고
둘 다 재시도 대상이다. API 503의 `retry-after`는 BFF가 그대로 넘기므로 브라우저 대기에 반영된다.

## B1. durable 세션 확인 캐시

middleware는 모든 관리 요청에서 서명 쿠키를 확인한 뒤 `POST /v1/admin/session-revocations/check`로
로그아웃 여부를 확인한다. 지도는 마커 묶음·상세·worker 등 여러 요청을 동시에 보내므로 요청마다
API 왕복과 `auth_limiter` 대기가 생기고, 저장소가 5초 안에 답하지 못하면 503이었다.

`lib/session.ts`의 `checkDurableSession`은 이제 다음을 지킨다.

- **GET/HEAD 요청의 active 판정만** 프로세스 메모리에 TTL 동안 재사용한다. 기본 10초,
  `WEATHER_UI_SESSION_CHECK_CACHE_SECONDS`로 바꾼다(최대 60초로 자른다). `0`은 캐시와 동시 확인
  공유를 모두 끈 이전 동작이다. 숫자가 아니거나 음수면 보수적으로 끈다.
- **revoked·unavailable은 저장하지 않는다.** 저장소 장애는 지금처럼 매 요청 503(fail-closed)이고,
  회복하면 다음 요청이 바로 통과한다.
- 같은 쿠키의 동시 확인은 진행 중인 요청 하나를 공유한다(single-flight). 그 요청이 실패하면 기다린
  요청도 모두 503이다.
- 키는 서명 쿠키 값 전체다. 백엔드 폐기 저장소도 같은 값의 digest를 보므로 sid와 1:1이고, 서명을
  확인하지 않은 값이 다른 쿠키의 판정을 대신할 수 없다. 모든 호출자는 서명을 먼저 확인한다.
- 항목 수는 1,024개로 제한하고 넘으면 가장 오래 넣은 판정부터 버린다(버려진 쿠키는 다시 확인한다).
- **상태 변경 요청은 캐시를 쓰지 않는다.** middleware의 POST/PATCH/PUT/DELETE(GET/HEAD 밖의 모든
  method)와 route handler의 Dagster console·주소 갱신 POST는 `checkDurableSession(..., { fresh: true })`로
  매번 저장소에 묻고 진행 중인 읽기 확인도 공유하지 않는다(`durableSessionCacheAllowed`). 새 확인이
  revoked·unavailable이면 그 쿠키의 읽기 캐시와 진행 중 확인의 저장도 버린다. 지도 폭주는 GET이라
  B1의 목적(확인 횟수 감소)은 그대로다.

### 로그아웃·재로그인과 반영 지연

- 같은 프로세스: middleware가 `/api/auth/logout`·`/api/auth/login` POST(허용 Origin, 서명이 맞는 기존
  쿠키)를 route로 넘기기 전에 그 쿠키를 캐시에서 지우고 60초 동안 다시 캐시하지 않는다. 60초는
  폐기 기록 쓰기 예산(5초)과 TTL 상한보다 길다. 진행 중이던 확인의 active 결과도 저장하지 않는다.
  route의 `revokeSessionValue`도 같은 프로세스 메모리의 판정을 지운다.
- **다른 web replica, 또는 같은 서버에서 다른 runtime의 캐시**(middleware는 Edge sandbox, route
  handler는 Node runtime이라 메모리가 다르다)는 로그아웃을 **GET/HEAD에 최대 TTL(기본 10초) 늦게**
  본다. 이 동안 탈취된 쿠키는 그 replica에서 읽기를 계속 통과할 수 있다. 상태 변경 요청은 캐시를 쓰지
  않으므로 바로 거부된다. 브라우저 자신은 로그아웃 응답이 쿠키를 지우므로 영향이 없다. 읽기 지연도
  없애야 하면 `WEATHER_UI_SESSION_CHECK_CACHE_SECONDS=0`으로 끈다.
- 서명·만료(8시간)·user-agent 지문·Origin 검사는 캐시와 무관하게 매 요청 그대로다.

### 백엔드 `auth_limiter`(2)는 유지

`app.py`의 `auth_limiter = CapacityLimiter(2)`는 바꾸지 않았다. 이 limiter는 거부하지 않고 대기만
시키며, 캐시 뒤 같은 세션의 확인은 TTL마다 최대 1번(동시 요청도 1번)으로 줄어든다. 관리 UI는 보통
관리자 한두 명이 쓰므로 2슬롯이 남는다. 늘리면 공개 조회와 같은 DB pool 연결을 더 점유할 수 있어
근거 없이 늘리지 않는다.

## B2. 공개 정적 자산은 middleware를 거치지 않는다

`public/maplibre/`(maplibre-gl worker 복사본, `scripts/copy-maplibre-worker.mjs`)는 공개 라이브러리
코드다. matcher를 `/((?!_next/static|_next/image|favicon.ico|maplibre/).*)`로 바꿔 인증 저장소가 느릴
때 worker가 503을 받아 지도가 타일을 하나도 요청하지 못하는 일을 없앤다. 접두사는 `maplibre/`까지
맞추므로 `/maplibre`, `/maplibre-x`, `/api/maplibre/...` 같은 다른 경로는 계속 middleware를 거친다
(`middleware-session.test.ts`의 matcher 시험).

## B5. GET 시간 상한과 재시도

### 브라우저 `lib/api.ts` `request()`

- GET은 시도마다 `AbortSignal.any([호출자 signal, AbortSignal.timeout(20초)])`로 헤더와 본문을 모두
  제한한다.
- 502/503/504, 네트워크 오류(`TypeError`), 20초 초과는 최대 2번 다시 보낸다(총 3번).
- **504와 20초 초과의 재시도는 공개 조회(`/v1/weather/*`)만 한다.** 공개 조회는 API DB 마감
  (`statement_timeout`)이 버려진 조회를 스스로 끝내지만, 관리 GET(`/v1/admin/*`: 동기화 이력·공급자·
  위치 관리 목록 등)은 그런 마감이 없어 BFF가 15초에 끊어도 API thread와 DB 조회가 계속 돈다. 이를
  다시 보내면 같은 느린 조회가 사용자 한 명당 최대 3개까지 겹치므로, 관리 GET의 504·시간 초과는
  바로 실패를 보여준다. 서버가 처리하지 않은 502·503(middleware 인증 저장소 503, BFF 전송 실패
  503 등)과 네트워크 오류는 관리 GET도 다시 보낸다.
- 대기는 지수 backoff(0.5초, 1초)와 `Retry-After`(초 또는 HTTP 날짜) 중 큰 값에 backoff의 절반까지
  jitter를 더한다. `Retry-After`가 10초를 넘으면 기다리지 않고 바로 실패를 보여준다.
- 호출자 signal 취소는 전송 중이든 대기 중이든 즉시 그 `AbortError`로 끝내고 더 보내지 않는다.
- 400·401·403·404·409·429·500 등은 다시 보내지 않는다. 다른 예외(코드 결함)도 그대로 올린다.
- 재시도가 끝나면 마지막 응답의 문장(`failureMessage`)을, 시간 초과면
  "날씨 서버 응답이 20초 안에 오지 않았습니다…"를 보여준다.
- **POST/PATCH/PUT/DELETE는 이전과 같이 한 번만 보내고 새 상한을 두지 않는다.** 서버가 이미 반영했을
  수 있기 때문이다.

최악의 경우 한 GET이 실패를 보여주기까지 약 3×20초+대기(약 2초, `Retry-After`가 있으면 그만큼)가
걸릴 수 있다. 보통은 BFF가 15초에 504를 주므로 약 3×15초+대기다.

### BFF `app/api/weather/[...path]/route.ts`

- **모든 GET**이 응답 헤더 수신 총예산 15초를 쓴다(이전에는 위치 목록·공급자 키 상태만 20초).
  넘으면 upstream을 끊고 `504`, `retry-after: 1`, `cache-control: no-store, private`로 끝낸다.
  BFF는 이 504를 다시 보내지 않는다. 재시도는 브라우저가 한다(관리 GET은 위 이유로 다시 보내지 않는다).
- upstream 응답의 `retry-after`는 `content-type`·`x-request-id`와 함께 그대로 넘긴다. 그래서 API 503
  (`READ_TIMEOUT`·`POOL_BUSY`·`DB_UNAVAILABLE`, 모두 `retry-after: 1`)의 대기 신호가 브라우저
  `retryDelayMs`까지 닿는다. upstream에 없으면 만들어 넣지 않는다.
- 위치 목록·공급자 키 상태의 헤더 전 socket 오류 1회 복구는 그대로이며 같은 15초 안에서 한다.
  이전 20초는 측정 기반 최적값이 아니라 초기 복구 예산이었고(`docs/reviews/2026-10-15-weather-location-proxy-recovery.md`),
  이제 브라우저 재시도가 남은 복구를 맡는다.
- 헤더 이후 본문 stream과 관리 write에는 상한을 두지 않는다.

## B4. 공개 조회 DB 연결 단절은 503 + Retry-After

`/v1/weather/*`에서 다음 예외는 500 대신 `503`, `retry-after: 1`, problem `code=DB_UNAVAILABLE`이다.

- SQLSTATE class `08`(connection_exception 계열)과 `57P01`·`57P02`·`57P03`(서버 종료·재시작·기동 중)
- SQLSTATE가 없는 단절: SQLAlchemy가 `connection_invalidated`로 판정했거나 `OperationalError`
  (server closed the connection unexpectedly, connection refused, connection timeout expired,
  접속 단계의 기동·종료 중 거부, too many clients)

SQLSTATE가 있는 권한(28xxx)·디스크(53100)·SQL 결함(42xxx)·무결성(23xxx)은 기다려도 같으므로 500으로
남긴다. 기존 `POOL_BUSY`·`READ_TIMEOUT`(57014) 분류는 그대로다. 관리 경로(`/v1/admin/*`)는 범위 밖이다.

### 접속 단계 설정 오류는 503이 아니다

psycopg 3은 **접속 단계 실패를 SQLSTATE 없이** 올린다(`connection failed: ...`, 서버 FATAL 응답의
SQLSTATE를 예외에 담지 않는다). 로컬 PostgreSQL 16에 실제로 접속해 보면 비밀번호 오류(28P01)·없는
DB(3D000)·없는 role 모두 `sqlstate=None`, `connection_invalidated=False`인 `OperationalError`였다.
SQLSTATE만 보면 DB 비밀번호 회전 누락 같은 영구 설정 오류도 연결 거부와 구별되지 않아
`503 DB_UNAVAILABLE` + `retry-after: 1`로 브라우저·PinVi가 계속 재시도하게 된다.

그래서 SQLSTATE 없는 `OperationalError` 가운데 원문에 다음 조각이 있으면
(`db_errors.PERMANENT_CONNECT_FAILURE_MARKERS`, 소문자 비교) 단절에서 빼고 처리되지 않은 오류(500,
traceback 로그)로 남긴다.

| 조각 | 원인 |
| --- | --- |
| `authentication failed` | 비밀번호(28P01), ident·peer·cert 인증(28000) |
| `no pg_hba.conf entry`, `pg_hba.conf rejects` | 접속 규칙 거부(28000) |
| `is not permitted to log in` | NOLOGIN role(28000) |
| `does not exist` | 없는 DB(3D000), trust 인증의 없는 role(28000) |
| `permission denied` | DB CONNECT 권한 없음(42501) |
| `no password supplied` | libpq: 비밀번호 미설정 |

서버 `lc_messages`가 영어가 아니면 조각이 맞지 않아 이전처럼 503으로 남는다(재시도 쪽으로 보수적).
연결 거부·시간 초과·기동/종료 중·연결 수 초과는 조각이 없어 계속 503이다. 실제 driver 시험
(`test_public_db_unavailable.py`의 `test_real_connect_misconfiguration_stays_500`·
`test_real_refused_connect_is_retryable_503`)이 이 경계를 고정한다.

분류는 `kortravelweather_api/db_errors.py` 하나로 모았고 service export의 일시 장애
(`EXPORT_TEMPORARILY_UNAVAILABLE`) 판정도 같은 함수를 쓴다. 그래서 접속 설정 오류는 service export에서도
일시 경합이 아니라 다른 영구 DB 오류와 같은 `EXPORT_NOT_READY`다. service export는 여기에 읽기 경합
SQLSTATE(57014·55P03·40P01·40001·53300)를 더 본다. 응답과 503 로그에는 원인 문장·SQL·주소·계정을 넣지
않고, 로그에는 SQLSTATE, 예외 타입 이름, DBAPI 예외 class 이름(`driver=ConnectionTimeout`·
`AdminShutdown`·`OperationalError` 등)만 남겨 지연·거부·종료를 구분한다. 읽기 단계 지표(`read_stage`
outcome)는 연결 단절을 `timeout`이 아니라 `error`로 남긴다.

## 확인하지 못한 것

- 운영(n150·odroid)에서 캐시 적중률·503 감소·지도 체감 시간은 측정하지 않았다. 배포 뒤
  web 로그의 `인증 저장소 응답을 확인할 수 없습니다` 503 건수와 API의 `session-revocations/check`
  요청 수를 전후 비교해야 한다.
- Edge middleware의 모듈 상태는 Next 서버 프로세스 안에서 유지된다(`next/dist/server/web/sandbox`의
  module context 캐시). 프로세스가 재시작되면 캐시는 비어서 다시 확인한다.
