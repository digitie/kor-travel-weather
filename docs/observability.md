# Prometheus 계측

API와 Dagster worker는 Prometheus text exposition 형식의 집계 지표를 제공한다.

| 대상 | scrape 주소 | 인증 | 설명 |
| --- | --- | --- | --- |
| FastAPI | `http://api:14101/metrics` | `Authorization: Bearer $KOR_TRAVEL_WEATHER_METRICS_TOKEN` | HTTP 요청/지연, sync lifecycle |
| Dagster worker | `http://dagster-code-server:14103/metrics` | compose 내부 network 전용 | multiprocess worker의 provider 호출/동기화 지표 |

기본 `compose.yaml`은 Prometheus를 `127.0.0.1:14104`에만 바인딩하고 두 target을
`deploy/prometheus/prometheus.yml`에서 scrape한다. Prometheus 데이터는
`prometheus-data` volume에 보존된다. n150의 HAProxy/public gateway에는 14104를
노출하지 않는다. 운영자는 SSH tunnel 또는 별도 인증된 관측 네트워크를 사용한다.
`deploy/prometheus/alerts.yml`에는 scrape 중단, API 5xx, provider 오류에 대한
기본 alert rule이 포함된다. 알림을 실제 담당자에게 전달하려면 운영 Prometheus의
Alertmanager receiver를 별도로 연결한다(이 Compose는 Alertmanager를 자동 노출하지 않는다).
**현재 n150에서 firing alert는 아무 데도 전달되지 않는다.** Manager의
`config/kor-travel-weather/prometheus.yml`에 `alerting:` 절(Alertmanager 대상)이 없어서, 규칙은
평가되고 `/api/v1/alerts`·`ALERTS` series로만 보인다. 전달이 필요하면 그 파일에
`alerting: {alertmanagers: [...]}`를 더하는 것이 Manager 쪽 작업이다.
Dagster는 `/var/run/kor-travel-weather-metrics` tmpfs를
`PROMETHEUS_MULTIPROC_DIR`로 공유해 multiprocess executor의 모든 worker 샘플을
14103 listener에서 합산한다. listener bind 충돌은
`ktw_metrics_server_bind_failures_total`로 확인할 수 있다.
정상 종료(SIGTERM/프로세스 exit)에는 worker가 자신의 live-gauge 파일을 정리하고,
비정상 종료(SIGKILL/OOM)로 남은 live-gauge 파일은 다음 scrape 때 PID 생존 확인으로
제거한다. 초기화 중 잘린/손상된 live-gauge 파일은 해당 scrape에서 격리하고, 죽은
writer의 파일은 함께 제거한다. 따라서 `*_active`가 오래 남는 경우 먼저 worker/container 상태와
`ktw_metrics_server_bind_failures_total`을 함께 확인한다.

API metrics는 기본 Compose에서 컨테이너당 단일 Uvicorn 프로세스를 전제로 한다.
API를 여러 replica로 확장할 때는 각 replica의 `/metrics`를 별도 Prometheus target으로
scrape하고 PromQL에서 합산하거나, 프로세스가 공유하는 전용
`PROMETHEUS_MULTIPROC_DIR`와 수명주기 정리 정책을 별도로 구성한다. 서로 다른
컨테이너가 임의의 로컬 multiprocess 디렉터리를 공유하면 PID 재사용으로 잘못된
샘플을 합칠 수 있으므로 지원하지 않는다.

## Secret 경계

`KOR_TRAVEL_WEATHER_METRICS_TOKEN`은 API의 전용 scrape bearer token이다. production
기동 시 16자 이상이고 admin token과 달라야 한다. 저장소나 이미지에 토큰을 기록하지
말고, Prometheus 컨테이너는 Compose native secret을
`/run/secrets/metrics.token`으로 읽는다. API `/metrics`는 Bearer token과 `x-metrics-token`만 허용하며
브라우저 cookie, admin token, query string token은 받지 않는다.

## 지표 계약

모든 날씨 서비스 지표는 `ktw_` 네임스페이스를 사용한다. 기존
`kor_travel_weather_` 이름은 더 이상 emit하지 않으므로 Grafana 패널·recording
rule·알람을 새 이름으로 함께 전환한다.

- `ktw_http_requests_total{method,route,status_class}`
- `ktw_http_request_duration_seconds{method,route}`
- `ktw_http_requests_in_flight{method}`
- `ktw_provider_requests_total{provider,dataset,outcome}`
- `ktw_provider_request_duration_seconds{provider,dataset}`
- `ktw_sync_runs_{started,finished,active}`
- `ktw_sync_{requests,source_records,values}_total`
- `ktw_sync_stale_recovered_total`
- `ktw_sync_locations_skipped_total{provider,dataset}` — lock 경합으로 다음 run에 맡긴 location 수(현재 KMA 특보)
- `ktw_sync_values_skipped_total{provider,dataset,reason}` — KMA 응답에서 버린 값 수. `reason=missing`은 Missing 센티널(|v| ≥ 900), `invalid`는 범위 밖/NaN. invalid가 하나라도 있거나, missing이 16건 이상이면서 시도한 값의 10%를 넘으면 run이 `partial`(KorTravelWeatherSyncFailed), 모든 값이 버려지면 `failed`. prod 규모(초단기실황 run당 약 1,368값)에서는 10% 비율이 먼저 걸린다: 약 137건, 즉 꺼진 측정소 대략 17~34곳(측정소마다 잃는 category 수에 따라)부터 paging한다. 16건 하한은 작은 run에서 측정소 하나의 결측이 paging하지 않게만 한다. 빈 값(공백)도 missing으로 센다. 그 아래 구간은 지표 자체의 규칙이 맡는다. 두 규칙 모두 `increase()`가 아니라 **현재 값 − 창 이전 값**을 본다: Dagster 합계는 worker별 mmap 파일을 합친 것이라, 파일 하나가 한 scrape 동안 빠지거나(살아 있는 worker가 쓰는 중 깨져 보임) 지워지면(죽은 worker의 깨진 파일) 합계가 줄고, `increase()`는 그것을 counter reset으로 읽어 counter 전체를 증가로 센다. 단순 차이는 감소로 읽고 조용하다. KMA worker는 import 때 `{python-kma-api} × {초단기실황, 초단기예보, 단기예보} × {missing, invalid, other}` child를 0으로 만든다(`initialize_sync_values_skipped`, multiprocess에서도 0 series가 나간다) — 그래서 첫 skip도 뺄 값이 있다. 양쪽 모두 10분 `max_over_time`으로 다듬어서, 지금이든 창 이전이든 scrape 하나가 stale이거나 한 scrape 동안 합계가 줄어도 결과가 바뀌지 않는다 — 울리던 alert가 풀리거나 `for`가 다시 시작되지 않는다. 10분보다 긴 공백이면 규칙은 아무것도 내지 않는다(오경보 대신 침묵). `KorTravelWeatherValuesMissingHigh`는 dataset별 3시간 30분 missing 증가가 60을 넘으면 pending이 되고 1시간 뒤 울린다(`for: 1h`는 지연일 뿐이다 — run 하나가 창에 3시간 30분 머문다). 임계값은 **초단기실황 기준**이다: 창에는 hourly run이 3~4개 들어가고, 꺼진 측정소 하나는 run당 7건이라 21~28건, 둘이면 42~56건으로 조용하며 셋(63~84건)부터 울린다. 리뷰가 지적한 15곳 결측(run당 105건, 10% 미만이라 run은 `success`)은 첫 run 1시간 뒤 울린다. 예보 dataset도 같은 규칙을 쓰는데, run당 값이 훨씬 많아 60은 작은 비율이고 예보 한 열이 비었다는 뜻이다. 창을 정확히 3시간으로 두면 run 끝나는 시각이 들쭉날쭉할 때(네 run이 3시간을 넘게 걸칠 때) 몇 분씩 run 둘만 보여 `for`가 초기화된다. 3시간 30분은 30분까지 버틴다. `KorTravelWeatherValuesInvalid`는 지난 1시간에 missing이 아닌 사유(`invalid`·`other`)가 하나라도 늘면 바로 울린다 — run도 `partial`이 되므로 dataset 이름을 붙여 주는 역할이다. 차이 방식의 대가는 모두 한 창(invalid 1시간, missing 3시간 30분) 동안이다. (1) **Dagster 컨테이너가 재시작되면** multiprocess tmpfs가 비어 counter가 0부터 다시 시작한다: 차이가 음수가 되어 skip을 보지 못한다. (2) 합계가 D만큼 **영구히** 줄면(죽은 worker의 깨진 파일 삭제) 그 뒤 D까지의 증가가 가려진다. (3) scrape 대상이나 `instance`·`job` label이 바뀌면 새 series에는 창 이전 값이 없어 두 규칙이 모두 침묵한다. 동작은 `tests/prometheus/alerts_test.yml`(CI `promtool test rules`)이 고정한다
- `ktw_sync_runs_overlap_skipped_total{provider,dataset}` — 같은 provider/dataset의 살아 있는 run(heartbeat가 3시간 lease 안)이 있어서 SUCCESS(skip)로 끝낸 시작 수. 실패가 아니며 `ktw_sync_runs_finished_total`에도 들어가지 않는다. 꾸준히 늘면 run이 자기 스케줄 간격보다 오래 걸린다는 뜻이다. lease가 만료된 앞 run과 겹치면 지금처럼 실패한다
- `ktw_forward_partition_days`, `ktw_oldest_partition_age_days` — API `/metrics` scrape 때 카탈로그에서 읽는다(API만 내보낸다). 앞의 것은 오늘 이후 날짜 파티션 일수(`KorTravelWeatherForwardPartitionsLow`, 3일 이하), 뒤의 것은 가장 오래된 날짜 파티션의 첫날부터 오늘까지 일수로 보존 정리의 정체 신호다(야간 정리 직후 `RETENTION_DAYS`, 다음 정리 직전 +1). `KorTravelWeatherRetentionStale`은 18(운영 16일 + 2) 초과가 30분 이어지면 울린다 — 정리가 이틀 연속 하루도 지우지 못했다는 뜻이다. API는 `RETENTION_DAYS`를 갖지 않으므로 임계값은 규칙에 있고 `tests/test_metrics.py`가 고정한다. 카탈로그 읽기가 실패하면 둘 다 빠지고 `KorTravelWeatherForwardPartitionsUnknown`이 울린다
- `ktw_metrics_errors_total{operation}`
- `ktw_metrics_server_up`
- `ktw_metrics_server_bind_failures_total`

라벨에는 location id, run/source key, 좌표, URL, credential이 들어가지 않는다. provider와
dataset은 현재 catalog allow-list 밖의 값이 `other`로 축약된다. `/metrics` 자체 요청은
HTTP request counter에서 제외해 scrape 주기가 트래픽을 오염시키지 않도록 한다.

## 규칙 변경 배포 (n150)

운영 weather Prometheus는 Manager compose의 `kor-travel-weather-prometheus`(host network
`:14104`)이고, Manager #452 이후 weather 체크아웃(`/home/digitie/kor-travel-weather`)의
`deploy/prometheus` 디렉터리를 `/etc/prometheus/weather-rules`로 읽기 전용 bind한다.
Prometheus는 규칙을 기동·reload 때만 읽으므로 `alerts.yml`만 바뀐 머지는 **체크아웃 갱신 +
SIGHUP**이면 된다. Manager의 weather `ensure`는 init step `weather-prometheus-rules-reload`로
SIGHUP을 자동으로 보낸다. `--web.enable-lifecycle`은 꺼져 있어 `/-/reload`는 없다.

주의할 것:

- 같은 체크아웃이 Manager의 weather 이미지 **build context**이고 `deploy/dagster.yaml`의 bind
  source다. 체크아웃을 앞으로 옮기면 다음 Manager rebuild/ensure가 그 코드로 이미지를 만든다.
  규칙만 반영하려는 것이어도 `origin/main`에 함께 들어온 다른 커밋이 무엇인지 먼저 본다.
- Python 코드도 바뀐 머지(예: skip counter를 0으로 만드는 `kma_weather` import)는 SIGHUP만으로는
  반영되지 않는다. Manager로 weather를 배포(`ensure`, code-server 재빌드)해야 하고, 그 init
  step이 SIGHUP까지 보낸다.

```bash
# PR이 main에 머지된 뒤, n150에서. 체크아웃이 main 위에 있는지부터 본다.
cd /home/digitie/kor-travel-weather
git status -sb | head -1          # "## main...origin/main", 변경 없음이어야 한다
git switch main                   # 다른 브랜치·detached면 main으로
git fetch origin
git log --oneline HEAD..origin/main   # 함께 들어올 커밋(빌드 context도 같이 움직인다)
git merge --ff-only origin/main
# 컨테이너가 보는 파일이 체크아웃과 같은지
md5sum deploy/prometheus/alerts.yml
docker exec kor-travel-weather-prometheus md5sum /etc/prometheus/weather-rules/alerts.yml
docker kill --signal=SIGHUP kor-travel-weather-prometheus
# reload 성공(reloadConfigSuccess=true, lastConfigTime 갱신)과 새 규칙 확인
curl -fsS http://127.0.0.1:14104/api/v1/status/runtimeinfo \
  | jq '.data | {reloadConfigSuccess, lastConfigTime}'
curl -fsS http://127.0.0.1:14104/api/v1/rules \
  | jq -r '.data.groups[].rules[].name'
```

규칙 파일이 깨졌으면 Prometheus는 옛 규칙을 지키고 `reloadConfigSuccess=false`를 낸다
(로그: `docker logs kor-travel-weather-prometheus`). CI의 `promtool check rules`·`test rules`가
머지 전에 이것을 막는다. 되돌리기는 체크아웃을 이전 커밋으로 돌리고 같은 SIGHUP이다.
firing alert는 아직 어디에도 전달되지 않는다(위 `alerting:` 참고). 확인은
`curl -fsS http://127.0.0.1:14104/api/v1/alerts`로 한다.

## 운영 확인

```bash
# local compose (token is read from the secret environment, never printed)
curl -fsS -H "Authorization: Bearer $KOR_TRAVEL_WEATHER_METRICS_TOKEN" \
  http://127.0.0.1:14101/metrics
curl -fsS http://127.0.0.1:14104/-/ready
docker compose ps prometheus
```

배포 후 `/version`의 commit과 Prometheus API의 target health를
확인한다. 로그만으로 scrape 성공을 판단하지 말고 다음처럼 `health=up` 및
`lastError`가 빈 값인지 확인한다.

```bash
curl -fsS http://127.0.0.1:14104/api/v1/targets \
  | jq '[.data.activeTargets[] | {job,health,lastError}]'
```

`up` 이후 `ktw_sync_runs_started_total`과
`ktw_provider_requests_total`의 증가를 확인하고, scrape 실패 시
API health와 weather ingest 자체가 계속 동작하는지 별도로 점검한다. Prometheus
container를 되돌릴 때는 API/Dagster를 중단하지 않고 `docker compose stop prometheus`
후 원인을 조사할 수 있다.
