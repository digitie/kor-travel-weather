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
| 5 | `markers` | 앞쪽 위치 60곳 × 최대 2묶음 `get_locations_by_ids` + `marker_values_many(limit_per_location=80, kinds=6종 전체)`. 사용자 마커와 같은 `marker_limiter`(2)를 나눠 쓴다 |
| 6 | `location_detail` | 첫 위치의 `get_location`·`latest_values(limit=200)`·`timeline(현재 시 정각~모레 0시 KST, limit=5000, exclude_alerts)` |

안전 장치:

- **읽기 전용.** 쓰기 함수를 부르지 않는다. 세션 확인의 무작위 값은 어떤 폐기 기록과도 맞지
  않아 lazy 삭제 분기에 들어가지 않는다. 시험이 실행한 문장에 쓰기가 없는지 확인한다.
- **동시성 1.** 단계를 한 task에서 차례로, DB 작업은 한 번에 worker thread 하나로 한다.
- **문장당 5초·전체 30초.** 공개 읽기 마감 장치(`read_limits.public_read_deadline`)에
  문장 상한(`statement_seconds`)을 더해 문장마다 `SET LOCAL statement_timeout = min(남은 예산, 5초)`를
  건다. pool 대기도 남은 예산으로 줄어든다(`ReadDeadlineQueuePool`). 예산이 끝나면 다음 문장은
  보내지 않는다.
- **사용자 우선.** 처리 중인 사용자 요청(`/v1/*`, 점검·문서 경로 제외)이 2건 이상이면 남은 단계를
  생략한다(`RequestContextMiddleware`가 센다). 단계 사이와 마커 묶음 사이에서 확인한다.
- **예외는 로그·지표만.** 단계가 실패하면 다음 단계로 간다. 단, 첫 단계(연결)가 실패하면 나머지는
  보내지 않는다(`reason=error`). 실패 로그는 원문(SQL·주소가 들어갈 수 있다) 대신 예외 형식·
  SQLSTATE·driver 예외 이름만 남기고, DB 밖 예외(코드 결함)만 traceback을 남긴다.
- **종료.** lifespan 종료 때 task를 취소하고 최대 6초 기다린다. 실행 중이던 문장은 5초 상한이 끝낸다.
- 지도 엔드포인트에는 결과 캐시가 없으므로 warmup이 오래된 응답을 남기지 않는다.

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
꺼져 있으면 null), `steps[]`(`name`·`outcome`=`ok|failed|skipped`·`elapsed_ms`). OpenAPI
`ReadinessOut`, 관리 웹 TS 타입은 `lib/api.ts`의 `ApiReadiness`·`getReadiness()`다.
uvicorn worker가 여럿이면 `/ready`는 요청을 받은 worker 하나의 상태다.

## 관측

- 지표 `ktw_api_prewarm_step_duration_seconds{step,outcome}`(histogram, 기동마다 단계당 1회).
- 로그 `weather_api_prewarm_step step=… outcome=… elapsed_ms=…`(INFO),
  `weather_api_prewarm_step_failed step=… type=… sqlstate=… driver=…`(WARNING),
  `weather_api_prewarm_finished warm=… reason=… elapsed_ms=… ok=… failed=… skipped=…`
  (예산 초과·연결 실패는 WARNING, 나머지는 INFO). API는 별도 logging 설정이 없어 기본 uvicorn
  실행에서는 WARNING 이상만 보인다. 정상 완료는 `/ready`·지표로 확인한다.

## 배포 확인

- `deploy/deploy.sh`는 `/version` commit 확인 뒤 `/ready`를 2초 간격으로 최대 60초 본다. 200이면
  본문을 출력하고, 끝내 200이 아니어도 경고만 남기고 배포는 성공으로 끝낸다. warmup은 첫 요청을
  빠르게 할 뿐이고 트래픽 차단 여부는 HAProxy/Manager가 정한다.
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
  빈 시험 DB라 warmup 전체가 수백 ms였고, 이 값은 운영 효과를 뜻하지 않는다.
- `pg_prewarm`은 확장 설치·`shared_preload_libraries` 변경이 필요하고 DB가 여러 테넌트 공유라
  넣지 않았다(검토 문서 §4.3).
