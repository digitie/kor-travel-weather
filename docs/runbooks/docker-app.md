# Docker/app runbook

1. Compose 환경 파일에 PostgreSQL/UI secret,
   `KOR_TRAVEL_WEATHER_ADMIN_TOKEN`, 별도의 16자 이상
   `KOR_TRAVEL_WEATHER_METRICS_TOKEN`,
   `KOR_TRAVEL_WEATHER_CREDENTIAL_ENCRYPTION_KEY`(Fernet URL-safe key)를 주입한다.
   VWorld 지도 타일을 사용하려면 공개 타일 식별자
   `NEXT_PUBLIC_VWORLD_API_KEY`도 주입한다(Next.js 빌드 시 고정되며, 빈 값이면
   중립 배경 fallback).
   API와 Dagster에 같은 encryption key를 전달해야 `/settings/providers`에서
   provider key override를 저장·복호화할 수 있다. `POSTGRES_PASSWORD`는
   `PGPASSWORD`로 전달되므로 URL-unsafe punctuation도 허용한다.
   Compose의 API/Dagster는 production profile을 강제로 사용하므로 admin token이
   없으면 기동하지 않는다.
   metrics token은 admin token과 달라야 하며 API Prometheus scrape 전용이다.
2. `deploy/deploy.sh`를 실행한다. `docker compose`를 손으로 직접 띄우지 않는다 —
   이 단계는 두 번 틀렸고, 스크립트는 두 경우를 모두 막는다.

   - **compose 파일**: `compose.yaml` 하나만 쓰면 모든 포트가 loopback에 묶인다.
     n150의 HAProxy는 Docker 밖에서 LAN 주소로 접속하므로, 그렇게 배포하면
     컨테이너는 전부 healthy인데 공개 hostname 3개가 모두 503을 답한다.
     `deploy/compose.n150.yaml`이 그 포트들을 LAN에 묶어 두므로, 배포 호스트의
     `.env`에 `COMPOSE_FILE=compose.yaml:deploy/compose.n150.yaml`을 넣어
     compose 명령이 무엇이든 이 override를 집어 들게 한다.
   - **`GIT_COMMIT`**: 이미지에 구워져 `/version`이 답하는 값이 된다. 빼먹으면
     `unknown`이 구워지고, 그러면 `/version`은 200과 함께 무엇과도 어긋날 수 없는
     값을 답한다 — 5번의 스모크 확인이 통과하면서 아무것도 확인하지 못한다.

   스크립트는 빌드를 detach해서 SSH가 끊겨도 중단되지 않게 하고, 확인할 주소를
   `docker compose port api 14101`로 compose에게 물어본 뒤 `/version`이 방금 배포한
   commit을 답할 때까지 기다린다. `migrate` one-shot 서비스가 `alembic upgrade head`를
   완료한 뒤 API/Dagster가 시작된다.
3. migration 복구가 필요하면 `docker compose -f compose.yaml run --rm migrate`를
   별도로 실행하고, `docker compose ... ps`에서 완료 상태를 확인한다.
4. API/Dagster/DB는 compose 기본값처럼 loopback/internal network에 두고,
   필요한 경우 인증된 gateway 또는 SSH tunnel만 web/API에 연결한다. DB port를
   인터넷에 직접 publish하지 않는다.
5. `/health`, `/version`, admin sync-runs에서 smoke를 확인한다. n150 운영 endpoint는
   API `https://weather-api.digitie.mywire.org`, Basic Auth로 보호된 Dagster
   `https://weather-dagster.digitie.mywire.org`(2026-09-30 공용 plane 전환 뒤에는
   `https://dagster.digitie.mywire.org` — Manager ADR-54; `14102`는 legacy profile로 운영에서
   기동하지 않는다, `deploy/n150.md`), web
   `https://weather.digitie.mywire.org`를 사용한다. 로컬 loopback 포트는 각각
   `14101`, `14102`, `14105`이며 PostgreSQL은 `14100`이다.
   Prometheus는 `14104` loopback에서만 동작한다(standalone compose 기준; Manager compose는
   `:14104` 전 인터페이스 bind — Manager `docker-compose.yml:3189` — 이고 `14103`도 `0.0.0.0` 기본,
   `src/kortravelweather/metrics.py:746`; LAN 경계는 호스트 방화벽, 아래 "odroid 운영 메모"). `curl -H
   "Authorization: Bearer $KOR_TRAVEL_WEATHER_METRICS_TOKEN"
   http://127.0.0.1:14101/metrics`와 `curl http://127.0.0.1:14104/-/ready`로 scrape를
   확인하고 public gateway에는 14104를 연결하지 않는다.

현재 migration head는 `0008_admin_login_rate_limits`다. 장시간 KMA 실행은 그룹마다
heartbeat를 갱신하므로, 180분 동안 heartbeat가 없는 running row만 자동으로 failed
회수된다.

Next.js admin은 내부 네트워크에서만 노출한다. Dagster 14102는
`dagster-gateway` Basic Auth 뒤에 있으며, web의 server-side GraphQL proxy만
내부 `dagster-webserver:14102`에 직접 접근한다. Web UI 자체는 kor-travel-geo와 같이
로그인 form에서 발급한 signed session만 사용하고 Basic Auth를 허용하지 않는다.
n150 reverse proxy에서 외부 접근을 허용할 때는 조직 SSO 또는 TLS로 보호된
세션 로그인 경계를 사용한다. `WEATHER_API_INTERNAL_URL`과 `WEATHER_ADMIN_TOKEN`은
server-side 환경변수로만 주입한다. Next proxy가 브라우저에 backend token을
전달하지 않도록 한다. web middleware는 로그아웃 marker를 API의 PostgreSQL에
기록하므로 web replica가 늘어나도 세션 폐기가 공유된다. 이를 위해 web 컨테이너의
`WEATHER_API_INTERNAL_URL`과 `WEATHER_ADMIN_TOKEN`을 API와 동일하게 설정한다.

서비스 key가 없거나 target이 비어 있으면 Dagster run은 실패해야 하며, 기존
immutable facts를 삭제하거나 부분 publish하지 않는다. MVP는 durable cursor 없이
매 실행에서 provider 응답을 재검증한다.

## odroid 운영 메모(계획, Manager ADR-58 proposed, 미구현)

- 위 1~5의 **로컬 compose 절차는 그대로다** — `compose.yaml`·`deploy/deploy.sh`·loopback 포트
  (`14100`/`14101`/`14102`/`14104`/`14105`)는 호스트와 무관하다. 운영은 이 저장소 compose가 아니라
  Manager compose(host network)이고 호스트는 Manager registry의 `weather` target이 정한다 — 지금
  n150, 계획은 odroid `192.168.1.13`. 운영 값(포트 동일·web `12205`·공용/자체 Dagster plane·전용
  PG `11000`·fresh DB·메모리·시계·전환 절차·odroid 상태)은 `deploy/n150.md` "계획: odroid 호스트"
  한 곳에 두고 여기 복제하지 않는다.
- 5의 운영 endpoint(`weather-api.`·`weather.`)는 HAProxy backend만 `.13`으로 바뀌고 이름은
  같다. odroid 자체 plane의 공개 host는 ADR-58 열린 결정 §14-4(`weather-dagster.` 재사용 또는
  새 이름; Manager `KTDM_PROD_URL_DAGSTER` 선행)로 아직 정해지지 않았다 — 결정 전에는 URL을 쓰지
  않고, 5의 Dagster 주소는 n150 공용 plane 링크(`dagster.`)를 쓴다. smoke는 `/version`의 `git_commit`과 odroid access log의 peer(`192.168.1.14` =
  PinVi)로 확인한다 — 200만으로 판단하지 않는다.
- 운영자가 odroid에서 손으로 하는 것(계획): 규칙 변경 SIGHUP(`docs/observability.md`, 같은 절차를
  odroid 체크아웃에서), `scripts/weather_values_forward_partitions.py`·`weather_values_purge_default.py`
  (`deploy/n150.md` "0017" 절, 컨테이너명 동일). 접속은 WSL에서 `ssh digitie@192.168.1.13`(직접,
  2026-10-13부터), root 작업은 `sudo -n`(NOPASSWD 설정 완료). 긴 작업은 ssh timeout 밖(tmux/nohup)에서.
- 보류된 소유자 결정 중 이 runbook에 닿는 것: `KOR_TRAVEL_WEATHER_RETENTION_AHEAD_DAYS=7`
  축소 여부(ADR-58 열린 결정 §14-17; `compose.yaml:66` 기본 7, 운영 `.env`로만 바뀐다) — 결정
  전에는 7 유지.
  `KOR_TRAVEL_WEATHER_RETENTION_DAYS`는 운영 7(2026-10-13 결정, `deploy/n150.md`)을 odroid root
  `.env`에도 명시한다.
