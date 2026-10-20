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
odroid 이전 뒤의 관측(federate)은 아래 "odroid 이전 뒤 관측" 절에 있다.

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
- `ktw_provider_requests_total{provider,dataset,outcome}` — `outcome`은 `success`·`error`·`blocked`.
  `blocked`는 provider의 edge(WAF)가 API에 닿기 전에 요청을 거절한 것이고, **여전히 실패다** —
  실패의 종류를 구분할 뿐이다. 지금은 `python-krex-api`만 분류한다: HTTP 400/403이면서 본문이
  "Request Blocked" 차단 페이지일 때만(`kortravelweather.providers.krex.upstream_block`; 태그·
  HTML entity·공백·대소문자 차이는 무시). 2026-10-08 04:35Z부터 krex WAF가 흔한 HTTP 라이브러리
  User-Agent(`python-httpx`, curl)를 이 페이지로 거절했다. 그 경우 `krex_restarea_sync` 단계는
  예전처럼 실패하고(job도 빨갛다), 오류와 step metadata에 `failure_kind=upstream_blocked`·
  `http_status`가 붙는다. 재시도는 없다: client는 400을 재시도하지 않고 `latest_weather`는 첫
  오류에서 멈추므로 차단된 run은 요청 1건이다. `KorTravelWeatherProviderErrors`는
  `outcome=~"error|blocked"`를 본다 — 분리 전처럼 차단도 provider 오류로 센다. 지역 관측망
  provider(`python-khoa-api`·`python-krforest-api`·`python-krex-api`)와 그 dataset은 label
  allow-list에 들어 있다(전에는 모두 `other`였다)
- `ktw_provider_request_duration_seconds{provider,dataset}`
- `ktw_sync_runs_{started,finished,active}`
- `ktw_sync_{requests,source_records,values}_total`
- `ktw_sync_stale_recovered_total`
- `ktw_sync_recovery_stalled` — `weather_sync_recovery` sensor가 정체 경보를 낸 뒤 1, 다음 성공 tick에서 0(code-server만 의미 있다; API도 0을 내보낸다). sensor는 common `reconciliation_sensor`의 정체 시계(sensor cursor의 `stall:` JSON)를 쓴다. 회수 콜백이 실패하거나(DB 장애, tick 예산 40초 초과 `DeadlineExceeded`) 두 회수 뒤에도 heartbeat가 lease(3시간)보다 오래된 running 행이 남으면(`SyncRunsStuck` — orchestrator가 아직 끝나지 않았다고 답하는, 프로세스가 살아 멈춘 worker. `deploy/dagster.yaml`의 18시간 정체) 첫 실패 뒤 15분에 Dagster sensor 로그에 `weather_sync_recovery.stalled key=… stalled_seconds=… last_failure=<예외 형식명[:SQLSTATE]> …` 오류를 남기고 이 gauge를 1로 둔다. 이후 1시간마다 로그를 다시 남긴다. `KorTravelWeatherSyncRecoveryStalled`는 dagster job의 이 값이 0보다 크면 바로 울린다(15분은 이미 sensor 안에 있다). code-server가 재시작되면 gauge는 0으로 시작하고, 정체가 이어지면 다음 재경보(최대 1시간) 때 다시 1이 된다 — 시계 자체는 cursor에 남는다. 18시간 정체와 같은 상황에서는 heartbeat가 멈춘 지 약 3시간 15분 뒤 울린다. 동작은 `tests/test_sync_worker_recovery.py`(실제 DB·sensor)와 `tests/prometheus/alerts_test.yml`이 고정한다
- `ktw_sync_locations_skipped_total{provider,dataset}` — lock 경합으로 다음 run에 맡긴 location 수(현재 KMA 특보)
- `ktw_sync_values_skipped_total{provider,dataset,reason}` — KMA 응답에서 버린 값 수. `reason=missing`은 Missing 센티널(|v| ≥ 900), `invalid`는 범위 밖/NaN. invalid가 하나라도 있거나, missing이 16건 이상이면서 시도한 값의 10%를 넘으면 run이 `partial`(KorTravelWeatherSyncFailed), 모든 값이 버려지면 `failed`. prod 규모(초단기실황 run당 약 1,368값)에서는 10% 비율이 먼저 걸린다: 약 137건, 즉 꺼진 측정소 대략 17~34곳(측정소마다 잃는 category 수에 따라)부터 paging한다. 16건 하한은 작은 run에서 측정소 하나의 결측이 paging하지 않게만 한다. 빈 값(공백)도 missing으로 센다. 그 아래 구간은 지표 자체의 규칙이 맡는다. 두 규칙 모두 `increase()`가 아니라 **현재 값 − 창 이전 값**을 본다: Dagster 합계는 worker별 mmap 파일을 합친 것이라, 파일 하나가 한 scrape 동안 빠지거나(살아 있는 worker가 쓰는 중 깨져 보임) 지워지면(죽은 worker의 깨진 파일) 합계가 줄고, `increase()`는 그것을 counter reset으로 읽어 counter 전체를 증가로 센다. 단순 차이는 감소로 읽고 조용하다. KMA worker는 import 때 `{python-kma-api} × {초단기실황, 초단기예보, 단기예보} × {missing, invalid, other}` child를 0으로 만든다(`initialize_sync_values_skipped`, multiprocess에서도 0 series가 나간다) — 그래서 첫 skip도 뺄 값이 있다. 양쪽 모두 10분 `max_over_time`으로 다듬어서, 지금이든 창 이전이든 scrape 하나가 stale이거나 한 scrape 동안 합계가 줄어도 결과가 바뀌지 않는다 — 울리던 alert가 풀리거나 `for`가 다시 시작되지 않는다. 10분보다 긴 공백이면 규칙은 아무것도 내지 않는다(오경보 대신 침묵). `KorTravelWeatherValuesMissingHigh`는 dataset별 3시간 30분 missing 증가가 60을 넘으면 pending이 되고 1시간 뒤 울린다(`for: 1h`는 지연일 뿐이다 — run 하나가 창에 3시간 30분 머문다). 임계값은 **초단기실황 기준**이다: 창에는 hourly run이 3~4개 들어가고, 꺼진 측정소 하나는 run당 7건이라 21~28건, 둘이면 42~56건으로 조용하며 셋(63~84건)부터 울린다. 리뷰가 지적한 15곳 결측(run당 105건, 10% 미만이라 run은 `success`)은 첫 run 1시간 뒤 울린다. 예보 dataset도 같은 규칙을 쓰는데, run당 값이 훨씬 많아 60은 작은 비율이고 예보 한 열이 비었다는 뜻이다. 창을 정확히 3시간으로 두면 run 끝나는 시각이 들쭉날쭉할 때(네 run이 3시간을 넘게 걸칠 때) 몇 분씩 run 둘만 보여 `for`가 초기화된다. 3시간 30분은 30분까지 버틴다. `KorTravelWeatherValuesInvalid`는 지난 1시간에 missing이 아닌 사유(`invalid`·`other`)가 하나라도 늘면 바로 울린다 — run도 `partial`이 되므로 dataset 이름을 붙여 주는 역할이다. 차이 방식의 대가는 모두 한 창(invalid 1시간, missing 3시간 30분) 동안이다. (1) **Dagster 컨테이너가 재시작되면** multiprocess tmpfs가 비어 counter가 0부터 다시 시작한다: 차이가 음수가 되어 skip을 보지 못한다. (2) 합계가 D만큼 **영구히** 줄면(죽은 worker의 깨진 파일 삭제) 그 뒤 D까지의 증가가 가려진다. (3) scrape 대상이나 `instance`·`job` label이 바뀌면 새 series에는 창 이전 값이 없어 두 규칙이 모두 침묵한다. 동작은 `tests/prometheus/alerts_test.yml`(CI `promtool test rules`)이 고정한다
- `ktw_sync_runs_overlap_skipped_total{provider,dataset}` — 같은 provider/dataset의 살아 있는 run(heartbeat가 3시간 lease 안)이 있어서 SUCCESS(skip)로 끝낸 시작 수. 실패가 아니며 `ktw_sync_runs_finished_total`에도 들어가지 않는다. 꾸준히 늘면 run이 자기 스케줄 간격보다 오래 걸린다는 뜻이다. lease가 만료된 앞 run과 겹치면 지금처럼 실패한다
- `ktw_forward_partition_days`, `ktw_oldest_partition_age_days` — API `/metrics` scrape 때 카탈로그에서 읽는다(API만 내보낸다). 앞의 것은 오늘 이후 날짜 파티션 일수(`KorTravelWeatherForwardPartitionsLow`, 3일 이하), 뒤의 것은 가장 오래된 날짜 파티션의 첫날부터 오늘까지 일수로 보존 정리의 정체 신호다(야간 정리 직후 `RETENTION_DAYS`, 다음 정리 직전 +1). `KorTravelWeatherRetentionStale`은 9(운영 7일 + 2, 2026-10-13부터; 이전 16일 + 2 = 18) 초과가 30분 이어지면 울린다 — 정리가 이틀 연속 하루도 지우지 못했다는 뜻이다. API는 `RETENTION_DAYS`를 갖지 않으므로 임계값은 규칙에 있고 `tests/test_metrics.py`가 고정한다. 카탈로그 읽기가 실패하면 둘 다 빠지고 `KorTravelWeatherForwardPartitionsUnknown`이 울린다
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
(odroid 이전 뒤에는 같은 경로·같은 절차를 **odroid에서** 한다 — 체크아웃과 Prometheus가 같은
호스트에 있다는 전제만 바뀌지 않으면 된다. 아래 "odroid 이전 뒤 관측" 참조.)
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

## odroid 이전 뒤 관측 — 중앙 scrape + federate(계획, Manager ADR-58 proposed, 미구현)

weather 스택이 odroid(`192.168.1.13`)로 옮겨가면(`deploy/n150.md` "계획: odroid 호스트")
관측은 Manager ADR-58 결정 11(관측 (b)) — **Prometheus·Grafana는 n150 하나**, odroid에는
exporter만 — 를 따른다. weather 자신의 Prometheus `14104`는 그 예외로 **1단계에서 odroid에
그대로 남고**, n150의 Manager Prometheus(`12102`)가 거기서 federate한다. ADR-58 본문의 소제목은
컨텍스트 / 결정(1~13) / 근거 / 결과(긍정·부정) / 관계 / 바뀌는 테스트 / 열린 결정(설계 §14 번호)이고
운영 절차는 Manager `docs/prod-deployment.md` §9다 — 아래 인용은 "결정 N"·"§14-N"을 쓴다. 두 호스트
Prometheus가 LAN에서 서로 닿는 전제는 bind가 loopback이 아니라는 것이다 — Manager compose의
weather Prometheus는 `--web.listen-address=:14104`(`docker-compose.yml:3189`, 전 인터페이스),
code-server metrics `14103`은 `0.0.0.0` 기본(`src/kortravelweather/metrics.py:746`); `deploy/n150.md`
머리의 "loopback에만"은 standalone compose 기준이다. LAN 경계는 odroid 호스트 방화벽이다.

- 1단계(플립 PR과 함께, Manager T-MGR-04/06): n150 `config/prometheus/prometheus.yml`에
  `weather-federate` job — `192.168.1.13:14104/federate`에서 `{__name__=~"ktw_.*"}`와
  `{__name__="ALERTS"}`를 가져온다. **match 값 주의**: 이 서비스의 지표 네임스페이스는 `ktw_`이고
  `kor_travel_weather_`·`weather_*`는 emit하지 않는다(위 "지표 계약"; 규칙 파일
  `deploy/prometheus/alerts.yml`의 `ktw_forward_partition_days` 등). Manager ADR-58 결정 11·
  `docs/tasks.md` T-MGR-04·`docs/prod-deployment.md` §9.5 본문과 설계 §10.2에 적힌 `weather_*`는
  설계 단계의 오기이며 그대로 구현하면 `/federate`가 빈 집합을 돌려준다(`up`은 1인데 weather
  series 0 — 조용한 실패). 구현 값은 `ktw_.*`이고 이 저장소 지표 계약이 정본이다; T-MGR-04
  구현 시 Manager 쪽 세 곳을 정정하고 수용 기준에
  `count({__name__=~"ktw_.*",host="odroid"}) > 0`을 둔다. 더해 `weather-code-server` job이
  `192.168.1.13:14103`을 **직접** scrape한다(무인증, 방화벽으로 n150만). weather API
  `14101`/`metrics`는 1단계에서 n150이 직접 scrape하지 **않는다** — bearer token
  (`KOR_TRAVEL_WEATHER_METRICS_TOKEN`)을 두 호스트 Prometheus에 중복 주입하지 않기 위해서다
  (odroid `14104`가 지금처럼 secret 파일로 읽고, n150은 그 결과를 federate로 받는다). 타깃은
  Manager registry에서 계산한 **커밋된 파생물**이고 결박 테스트가 지킨다(ADR-58 결정 5·Manager
  `docs/bindings.md` B-8).
- 왜 `14104`를 바로 퇴역시키지 않는가: 이 저장소의 `deploy/prometheus/alerts.yml`(규칙 10개)은
  Manager compose가 weather **체크아웃 디렉터리**를 bind해 읽는다(Manager
  `config/docker-targets.yml:969-971`, 위 "규칙 변경 배포"). 체크아웃·규칙·SIGHUP 경로가 전부
  호스트 로컬이라 odroid로 옮겨도 그대로 성립하고, n150에서 체크아웃을 지우는 해체(Manager
  `docs/prod-deployment.md` §9.6) 뒤에는 n150에 규칙 정본이 없다. 2단계 퇴역(시점은 소유자 결정,
  ADR-58 열린 결정 §14-11)에서 token·규칙 자리를 n150 Prometheus로 옮긴 뒤에만 `14104`를 내린다.
- **federate는 샘플 타임스탬프에 의존한다.** `/federate` 응답은 odroid Prometheus가 scrape한
  시각을 달고 오므로 odroid 시계가 틀리면 n150 Prometheus가 out-of-bounds로 **조용히 버린다**
  (`up`은 1인데 `ktw_*`가 비는 모양). 직접 scrape(`14103`)는 n150 시각을 쓰므로 영향이 없다.
  그래서 n150 쪽 시계 rule이 먼저 설명한다: `node_timex_sync_status{host="odroid"} == 0` 또는
  `abs(node_time_seconds - timestamp(node_time_seconds)) > 5`. node_exporter는 두 호스트 모두 아직
  없고 추가 예정이다(포트는 Manager `docs/ports.md` 절차, 후보 `12106`); timesyncd 부팅 순서
  영속화(Manager `docs/prod-deployment.md` §9.1 1단계 ⓪~④ — drop-in, 2026-10-13 현재 미완)
  전까지 이 rule의 첫 상태는 firing이 정상이다. odroid 시계가 weather 자체에 미치는 영향(파티션
  바닥·forward window)은 `deploy/n150.md` "시계" 절.
- Alertmanager는 여전히 없다. 이 저장소 규칙의 firing은 federate된 `ALERTS` series로 n150
  Grafana에서 보이지만 **전달되지 않는다**(문서 첫 절의 `alerting:` 단락과 같은 상태). 최소
  신호는 n150 Prometheus의 `count(up{host="odroid"}) == N ∧ 전부 1` — `up{host="odroid"}`가 전부
  0이면 odroid 다운뿐 아니라 DHCP 임대 변경(주소 이동)도 의심한다. odroid Prometheus에도
  `--web.enable-lifecycle`은 없다(변경 없음).
- 이 저장소에서 바뀌는 것: 없음. `deploy/prometheus/prometheus.yml`(standalone, compose DNS 타깃)과
  `alerts.yml`은 그대로이고, Manager의 `config/kor-travel-weather/prometheus.yml` override도
  `127.0.0.1` 타깃이라 호스트와 무관하다.

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
