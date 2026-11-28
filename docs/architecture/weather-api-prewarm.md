# API 시작 warmup과 `/ready`

[Map 호출 검토](reports/2026-11-24-map-call-performance-review.md) §4.3 경로 B, 권장 순서 6번의
구현 기록이다. 관리 지도(`/v1/weather/*`)의 배포 직후 첫 호출 비용을 줄이고, 배포 확인이
그 상태를 볼 수 있게 한다.

## 왜

배포 직후 관리 지도 마커 60곳 묶음은 26.8/25.5/25.3초, 이어진 묶음은 14.6/15.6/14.9초였다
(`docs/reports/2026-10-28-marker-alert-prefix.md`, odroid). 차이 약 10초가 1회성 비용이다.
빈 DB pool(10+10, pre-ping)·연결별 prepare/plan·SQLAlchemy 컴파일 캐시·지연 import
(`kortravelcommon.address_providers`)·PG 버퍼가 첫 사용자 요청에 몰린다. API에는
lifespan/startup hook이 없었고 `/health`는 DB를 보지 않는 고정 응답이었다.

Map export(`/v1/service/exports/*`)는 이 warmup으로 거의 풀리지 않는다(캐시 60초·프로세스
메모리, 예보 첫 페이지는 캐시를 쓰지 않았다). 그 경로는 사전 구성(A3)의 몫이라 여기서 두드리지
않는다.

## 동작

`create_app`의 lifespan이 포트 열기를 기다리게 하지 않는 `asyncio` task 하나를 만든다
(`packages/kor-travel-weather-api/src/kortravelweather_api/prewarm.py`). 관리 지도가 처음 밟는
읽기 경로를 사용자 요청과 같은 인자로 차례로 한 번 밟는다.

| 순서 | 단계(`steps[].name`) | 하는 일 |
| --- | --- | --- |
| 1 | `connections` | 지연 import(`kortravelcommon.address_providers`·`reverse_address`), 연결 3개를 한 번에 쥐고 각각 `SELECT 1` 뒤 pool에 반환 |
| 2 | `session_check` | `is_admin_session_revoked`(무작위 값, `auth_limiter` 공유). BFF가 모든 관리 요청마다 5초 fail-closed로 부르는 경로 |
| 3 | `regions` | `region_locations()` 읽기만. 쓰기 `refresh_region_locations`는 부르지 않는다 |
| 4 | `locations` | `list_locations(limit=1000, include_metadata=False)`·`count_locations`·`public_location_regions` (`GET /v1/weather/locations?limit=1000`과 같다) |
| 5 | `markers` | 카탈로그 앞쪽 120곳(사용자 60곳 묶음 2개 분량)을 **15곳씩 최대 8묶음**으로 `get_locations_by_ids` + `marker_values_many(limit_per_location=80, kinds=6종 전체)`. 값 읽기는 라우터처럼 공유 읽기 묶음(`read_scope`) 안에서 한다. 묶음마다 사용자 마커와 같은 `marker_limiter`(2)에서 한 자리를 잡았다 돌려준다. 실패한 묶음은 로그만 남기고 다음 묶음으로 간다. 아래 "마커 몫"까지만 한다 |
| 6 | `location_detail` | 첫 위치의 하위 읽기 셋을 차례로: `location`=`get_location`, `latest`=`latest_values(limit=200)`, `forecast`=`timeline(현재 시 정각~모레 0시 KST, limit=5000, exclude_alerts)`. 하위 읽기마다 따로 연결·transaction을 쓰고, 시작 전에 문장 마감까지 문장 상한 하나(5초)가 남지 않으면 남은 하위 읽기를 시작하지 않는다. 실패한 하위 읽기는 로그만 남기고 다음으로 간다 |

안전 장치:

- **읽기 전용.** 쓰기 함수를 부르지 않는다. 세션 확인의 무작위 값은 어떤 폐기 기록과도 맞지
  않아 lazy 삭제 분기에 들어가지 않는다. 시험이 실행한 문장에 쓰기가 없는지 확인한다.
- **동시성 1.** 단계를 한 task에서 차례로, DB 작업은 한 번에 worker thread 하나로 한다.
- **문장당 5초·전체 30초.** 공개 읽기 마감 장치(`read_limits.public_read_deadline`)에
  문장 상한(`statement_seconds`)을 더해 문장마다 `statement_timeout`이 `min(남은 예산, 5초)` 이하가
  되게 한다. 값은 transaction마다 bind 상수 SQL(`set_config`)로 한 번 걸고, 남은 예산과 어긋날 때만
  다시 건다(`lock_timeout` 2초도 같이 건다, [weather-public-read-path.md](weather-public-read-path.md)).
  pool 대기도 남은 예산으로 줄어든다(`ReadDeadlineQueuePool`). 예산이 끝나면 다음 문장은
  보내지 않는다.
- **마감 여유.** 문장 마감(“남은 예산”의 기준)은 전체 예산 끝에서 마감 여유
  (`PREWARM_FINISH_MARGIN_SECONDS`, 0.5초)를 뺀 시각, 운영 값으로 시작 후 29.5초다.
  `statement_timeout`은 다시 걸기 여유(50ms)와 왕복만큼 마감보다 늦게 문장을 끊을 수 있고,
  끊긴 뒤 rollback·연결 반환·thread 복귀가 더 든다. 그 몫을 예산 안에 둬 전체 `elapsed_ms`가
  30초를 넘지 않게 한다. 단계·마커 묶음·상세 하위 읽기도 문장 마감까지만 시작한다.
- **마커 몫.** 마커 단계의 마감은 문장 마감에서 상세 단계 몫(문장 상한 하나 5초와 마감 여유
  0.5초)을 뺀 시각이다. 마감에 잘린 마커 묶음의 정리도 마감 여유 안에 끝나므로, 상세 단계는
  시작할 때 첫 하위 읽기에 문장 상한 하나를 온전히 쓸 수 있다. 마커 몫이 문장 상한 하나보다
  적게 남으면 새 묶음을 시작하지 않는다(시작해도 마감에 잘릴 가능성이 크다). 마커 자리 대기도
  그때까지만 한다. 운영 값으로는 시작 후 19초까지 새 묶음을 시작하고 24초에 마커를 끝낸다
  (#309는 20초·25초였다).
- **사용자 우선.** 처리 중인 사용자 요청(`/v1/*`, 점검·문서 경로 제외)이 2건 이상이면 남은 단계를
  생략한다(`RequestContextMiddleware`가 센다). 단계 사이와 마커 묶음 사이, 마커 자리를 얻은
  직후에 확인한다. 자리를 기다리는 사이 사용자 요청이 몰렸으면 묶음을 보내지 않고 자리를 돌려준다.
- **예외는 로그·지표만.** 단계가 실패하면 다음 단계로 간다. 마커는 묶음 하나가 실패해도 다음
  묶음으로 가고, 하나라도 실패하면 단계 결과는 `failed`다(데운 위치 수는 `warmed`로 따로 보인다).
  상세 단계도 같다. 하위 읽기 하나라도 실패(문장 상한·마감에 잘린 57014 포함)하면 `failed`이고,
  실패 없이 예산 때문에 남은 하위 읽기를 시작하지 않았으면 `ok`에 `warmed < planned`다.
  단, 첫 단계(연결)가 실패하면 나머지는
  보내지 않는다(`reason=error`). 실패 로그는 원문(SQL·주소가 들어갈 수 있다) 대신 예외 형식·
  SQLSTATE·driver 예외 이름만 남기고, DB 밖 예외(코드 결함)만 traceback을 남긴다.
- **종료.** lifespan 종료 때 task를 취소하고 최대 6초 기다린다. asyncio 취소는 worker thread를
  멈추지 못하므로(anyio `to_thread`) 취소 신호(`threading.Event`)를 thread에도 보낸다. thread는
  다음 문장을 보내지 않고(`read_limits`의 `before_cursor_execute`가 거절), task는 실행 중이던
  문장(5초 상한)이 끝나 thread가 돌아온 뒤 끝난다. pool 대기·연결 수립처럼 문장이 아닌 대기가
  길어지면 6초 안에 종료를 계속하고, 그 thread도 다음 문장은 보내지 않는다.
- 지도 엔드포인트에는 결과 캐시가 없으므로 warmup이 오래된 응답을 남기지 않는다.

## 마커를 15곳씩 나누는 이유

처음 구현(#298)은 사용자와 같은 60곳 묶음을 2번 읽었다. 최신 main(`62c67bd`)을 odroid에
배포한 직후 `/ready`는 다음과 같았다.

```
warm=skipped reason=budget elapsed_ms=30152
connections ok 1099 · session_check ok 373 · regions ok 748 · locations ok 1043
markers failed 26883 · location_detail skipped
weather_api_prewarm_step_failed step=markers type=OperationalError sqlstate=57014 driver=QueryCanceled
```

- 앞 네 단계가 3.3초를 쓰고 마커는 남은 26.7초를 받았다. 차가운 60곳 묶음 하나가 그 안에 끝나지
  않아 문장 마감(`min(남은 예산, 5초)`)에 걸려 57014로 끝났다(26.9초 ≈ 남은 예산). 묶음 하나가
  단위라 부분 성과가 없고(데운 위치 0), 상세 단계는 예산이 없어 생략됐다. 그동안 사용자 마커 자리
  두 개 중 하나도 27초 동안 warmup이 쥐었다.
- 같은 묶음의 문장들은 차가울 때 5초 상한도 넘는다. odroid `pg_stat_statements`(11-17 이후)의
  60곳 묶음 문장 최댓값: 사실 수화(`current_fact_keys` join) 12.4초, 특보 후보 cursor 9.7초,
  특보 구역 판정(`scope_fact_keys`) 4.0초, 현재값 후보 3.0초. 위치 수에 비례하는 몫을 4로 나누면
  (15곳) 3.1초 이하로 상한 안에 든다.
- 데운 뒤에도 60곳 묶음은 6~13초였고(odroid 2026-11-25 읽기 전용 측정, `x-duration-ms`),
  10~20곳 요청은 0.9~4.7초였다. 15곳 묶음은 데운 상태면 8묶음 모두 마커 몫 안에 들고, 차가운
  상태에서도 묶음마다 끝나 앞쪽부터 일부라도 데운다. 데운 양은 `/ready`의 `warmed`/`planned`로
  배포마다 보인다.
- 묶음이 작으면 사용자 마커 자리를 묶음 하나(수 초) 동안만 쥔다. 연결 checkout도 묶음마다 따로라
  pool의 여러 연결이 차례로 같은 문장을 밟는다.

시험(`test_api_prewarm.py`)은 예산·상한을 1/5(6초·1초)로 줄이고 마커 읽기에 위치당 0.04초를
들게 해 이를 재현한다. 60곳 묶음은 두 묶음 모두 57014로 `warmed=0`이고, 15곳 묶음은 로컬에서
`warmed=105/120`(7묶음)을 데운 뒤 상세 단계까지 `ok`로 4.5초에 끝났다(#309 시점, 마감 여유 전).
로컬 값이며 운영 효과를 뜻하지 않는다.

## 상세 단계를 하위 읽기로 나누고 마감 여유를 두는 이유

#309(15곳 묶음)를 담은 main(`4932cbe`)을 odroid에 배포한 직후(2026-11-26 10:17 KST, API와
Dagster code server가 같이 재시작해 DB가 차가웠다) `/ready`는 다음과 같았다.

```
warm=done elapsed_ms=30113
connections ok 1019 · session_check ok 300 · regions ok 703 · locations ok 1099
markers ok 19907 warmed=60/120 · location_detail failed 7077
weather_api_prewarm_step_failed step=location_detail type=OperationalError sqlstate=57014 driver=QueryCanceled
```

- 직전 배포(11-25 19시대, 역시 재시작)는 `elapsed_ms=22669`, 마커 120/120(19131ms), 상세 `ok`
  1583ms였다. 이번에는 DB가 더 차가워 마커 15곳 묶음이 약 5초씩 걸렸고 4묶음(60곳)이 시작 후
  23.0초에 끝났다(다섯째 묶음은 새 묶음 시작 시각 20초를 넘겨 시작하지 않았다).
- 상세 단계는 23.0초에 남은 7.0초를 받았다. 예전에는 위치·최신값·예보 세 읽기(문장 다섯: 위치
  PK, 최신값 후보·사실 수화, 예보 후보·사실 수화)를 worker 호출 하나로 읽었고, 마지막 문장이
  예산 마감에 잘렸다. PostgreSQL 로그의 잘린 문장(01:17:49.660 UTC)은 사실 수화
  (`weather_values JOIN unnest(...) AS current_fact_keys`)였다. 같은 문장은 `pg_stat_statements`
  (11-17 이후)에서 평균 446ms·최댓값 12.4초이고, 상세의 후보 문장은 최신값 후보가 최댓값 2.6초,
  예보 후보가 1.2초였다. 잘린 문장은 통계에 남지 않으므로 이번 값은 "남은 몫 이상"까지만 안다.
- 전체가 30113ms로 예산을 113ms 넘겼다. 문장 마감이 예산 끝과 같아, 마감에 잘린 문장의 정리
  (`statement_timeout`이 마감보다 조금 늦게 끊는 몫, rollback·연결 반환·thread 복귀)가 예산 밖으로
  나갔다. 단계가 마감 뒤에 시작한 것은 아니다(상세 단계는 23.0초에 시작했다).

그래서 상세 단계를 하위 읽기 셋으로 나눠 하위 읽기마다 남은 예산을 다시 보고, 문장 상한 하나만큼
남지 않으면 남은 하위 읽기를 시작하지 않는다(마커 묶음과 같은 규칙). 문장 마감은 예산 끝보다
마감 여유 0.5초 앞에 둬 잘린 문장의 정리까지 예산 안에 든다. 문장 상한(5초)과 예산(30초)은
그대로이고, 잘린 하위 읽기는 계속 `failed`와 57014 로그로 보인다. 같은 운영 상황이라면 상세
단계는 29.5초까지 6.5초를 받아 위치·최신값을 읽고, 최신값 뒤 5초가 남지 않으면 예보는 시작하지
않아 `ok`·`warmed=2/3`으로 30초 안에 끝난다.

시험은 예산·상한을 1/5(6초·1초)로 줄이고 마커가 몫을 다 쓰게 한 뒤 하위 읽기에 0.3/0.9/0.9초를
들게 한다. 예전 방식은 6002ms(예산 초과)·상세 `failed`(57014)였고, 지금은 `ok`·`warmed=2/3`으로
6초 안에 끝난다. 예산 마감에 잘린 문장(시작 단계의 `pg_sleep`) 시험도 예전 2002ms에서 2초 안으로
들어온다. 로컬 값이며 운영 효과를 뜻하지 않는다.

## 설정

`KOR_TRAVEL_WEATHER_API_PREWARM_ENABLED`. 비워 두면 production 프로필(`KOR_TRAVEL_WEATHER_ENV`
기본값)에서만 켜진다(`WeatherSettings.api_prewarm_active`). 운영 Manager compose가 따로 넘기지
않아도 production이면 동작하고, 시험·로컬(development)은 시작 때 DB를 따로 읽지 않는다.
`true`/`false`로 명시하면 그 값을 쓴다. `create_app`에 `WeatherRepository`가 아닌 repository
double을 넘기면 설정과 무관하게 끈다. 저장소 `compose.yaml`의 api 서비스는
`${KOR_TRAVEL_WEATHER_API_PREWARM_ENABLED:-true}`로 명시한다.

## `/ready`

`/health`는 그대로 생존 확인(고정 응답)이다. 새 `GET /ready`는 이 프로세스의 warmup 상태를
돌려주며 DB를 읽지 않는다(DB 건강 검사가 아니다).

| `warm` | 뜻 | HTTP |
| --- | --- | --- |
| `running` | 진행 중 | 503 (`retry-after: 1`) |
| `done` | 모든 단계를 시도함(일부 `failed`일 수 있다) | 200 |
| `skipped` | 남은 단계를 생략함. `reason`: `budget`(30초 초과)·`busy`(사용자 요청 2건 이상)·`error`(연결 실패)·`shutdown` | 200 |
| `disabled` | 꺼져 있음(또는 lifespan 미실행) | 200 |

본문: `ready`(200이면 true), `warm`, `reason`, `elapsed_ms`(시작부터 끝까지, 진행 중이면 지금까지,
꺼져 있으면 null), `steps[]`(`name`·`outcome`=`ok|failed|skipped`·`elapsed_ms`·`warmed`·
`planned`). `warmed`/`planned`는 마커와 상세 단계만 채운다. 마커는 읽기를 마친 위치 수(성공한
묶음의 위치 합)와 데우려던 위치 수(앞쪽 최대 120곳), 상세는 마친 하위 읽기 수와 하위 읽기 수(3)다.
다른 단계와 위치가 없어 생략된 단계는 null이다. OpenAPI
`ReadinessOut`, 관리 웹 TS 타입은 `lib/api.ts`의 `ApiReadiness`·`getReadiness()`다.
uvicorn worker가 여럿이면 `/ready`는 요청을 받은 worker 하나의 상태다.

공개 범위: `/ready`는 `/health`·`/version`처럼 토큰 없이 공개 base URL에서 열린다. 본문은 단계
이름·결과·시간과 생략 이유뿐이고 SQL·주소·호스트·예외 원문은 담지 않는다. 기동 직후 DB 연결
실패(`reason=error`, `connections`=`failed`)와 기동 시각이 익명에게 보이는 것은 의도한 선택이다.
배포 확인(`deploy.sh`, 이후 Manager verify)이 metrics token 없이 이 신호를 받아야 하기 때문이다.
같은 단계 지표(`ktw_api_prewarm_step_duration_seconds`)를 포함한 나머지 운영 지표는 `/metrics`가
계속 전용 token으로 보호한다.

## 관측

- 지표 `ktw_api_prewarm_step_duration_seconds{step,outcome}`(histogram, 기동마다 단계당 1회).
- 로그 `weather_api_prewarm_step step=… outcome=… elapsed_ms=…`(INFO, 마커·상세는 `warmed=…
  planned=…` 추가), `weather_api_prewarm_step_failed step=… type=… sqlstate=… driver=…`(WARNING,
  마커 묶음 실패는 `step=markers chunk=2/8 …`, 상세 하위 읽기 실패는
  `step=location_detail read=latest …`처럼 묶음 번호·하위 읽기 이름을 붙여 하나마다 한 줄),
  `weather_api_prewarm_finished warm=… reason=… elapsed_ms=… ok=… failed=… skipped=…`
  (예산 초과·연결 실패는 WARNING, 나머지는 INFO). API는 별도 logging 설정이 없어 기본 uvicorn
  실행에서는 WARNING 이상만 보인다. 정상 완료는 `/ready`·지표로 확인한다.

## 배포 확인

- `deploy/deploy.sh`는 `/version` commit 확인 뒤 `/ready`를 2초 간격으로 최대 60초 본다. 200이면
  본문을 출력하고, 끝내 200이 아니어도 경고만 남기고 배포는 성공으로 끝낸다. warmup은 첫 요청을
  빠르게 할 뿐이고 트래픽 차단 여부는 HAProxy/Manager가 정한다.
- 200 본문에 실패 단계(`"outcome":"failed"`)나 연결 실패(`"reason":"error"`)가 있으면 stderr에
  경고 한 줄을 더 남긴다. `/version`·`/health`는 DB를 보지 않으므로 새 배포에서 DB 도달 여부를
  알려 주는 신호는 이것뿐이다. 종료 코드는 그대로 0이다.
- compose `web` 서비스에 healthcheck를 추가했다. 자격증명 없이 `GET /login`만 보내고
  (`redirect: manual`, 200만 정상) 첫 점검이 Next route 모듈·middleware를 올려 둔다. 세션 cookie가
  없으므로 API를 부르지 않는다. interval 30초·timeout 5초·retries 12·start_period 30초.
- api compose healthcheck는 `/health` 그대로다. warmup 중(503)이라고 컨테이너를 unhealthy로
  만들지 않는다.

## 범위 밖·남은 일

- 운영은 Manager compose(host network)다. Manager의 배포 verify가 `/ready`를 같은 방식(최대 60초,
  초과는 경고)으로 보게 하는 것, web healthcheck를 Manager 선언에 옮기는 것은 Manager 저장소 일이다.
  실제 트래픽 게이트(HAProxy가 `/ready`를 보는 것)도 그쪽 결정이다.
- 효과 수용 기준(배포 직후 첫 마커 묶음 대 두 번째 묶음 차이)은 운영 배포 뒤 측정한다. 로컬에서는
  빈 시험 DB라 warmup 전체가 수백 ms였고, 이 값은 운영 효과를 뜻하지 않는다. 배포 직후 `/ready`의
  마커 `warmed`/`planned`·`outcome`과 `location_detail`의 `outcome`·`warmed`/`planned`, 전체
  `elapsed_ms`(30000 이하)를 함께 본다. 상세가 `failed`면 API 로그의 `read=…`로 잘린 하위 읽기를,
  PostgreSQL 로그의 `canceling statement due to statement timeout`·`STATEMENT`로 잘린 문장을 본다.
- warmup이 데우는 위치는 카탈로그 앞쪽이고 사용자의 첫 화면 위치와 다를 수 있다. 위치와 무관한
  몫(연결·catalog/plan cache·SQLAlchemy 컴파일·인덱스 상위 page)은 어느 위치로 데워도 같다.
- `pg_prewarm`은 확장 설치·`shared_preload_libraries` 변경이 필요하고 DB가 여러 테넌트 공유라
  넣지 않았다(검토 문서 §4.3).
