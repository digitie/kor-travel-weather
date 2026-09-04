# Prometheus 계측

API와 Dagster worker는 Prometheus text exposition 형식의 집계 지표를 제공한다.

| 대상 | scrape 주소 | 인증 | 설명 |
| --- | --- | --- | --- |
| FastAPI | `http://api:14101/metrics` | `Authorization: Bearer $KOR_TRAVEL_WEATHER_METRICS_TOKEN` | HTTP 요청/지연, sync lifecycle |
| Dagster worker | `http://dagster:14103/metrics` | compose 내부 network 전용 | multiprocess worker의 provider 호출/동기화 지표 |

기본 `compose.yaml`은 Prometheus를 `127.0.0.1:14104`에만 바인딩하고 두 target을
`deploy/prometheus/prometheus.yml`에서 scrape한다. Prometheus 데이터는
`prometheus-data` volume에 보존된다. n150의 HAProxy/public gateway에는 14104를
노출하지 않는다. 운영자는 SSH tunnel 또는 별도 인증된 관측 네트워크를 사용한다.
`deploy/prometheus/alerts.yml`에는 scrape 중단, API 5xx, provider 오류에 대한
기본 alert rule이 포함된다. 알림을 실제 담당자에게 전달하려면 운영 Prometheus의
Alertmanager receiver를 별도로 연결한다(이 Compose는 Alertmanager를 자동 노출하지 않는다).
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
- `ktw_metrics_errors_total{operation}`
- `ktw_metrics_server_up`
- `ktw_metrics_server_bind_failures_total`

라벨에는 location id, run/source key, 좌표, URL, credential이 들어가지 않는다. provider와
dataset은 현재 catalog allow-list 밖의 값이 `other`로 축약된다. `/metrics` 자체 요청은
HTTP request counter에서 제외해 scrape 주기가 트래픽을 오염시키지 않도록 한다.

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
