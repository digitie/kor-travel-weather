# Docker/app runbook

1. Compose 환경 파일에 PostgreSQL/UI secret과
   `KOR_TRAVEL_WEATHER_ADMIN_TOKEN`을 주입한다. `POSTGRES_PASSWORD`는
   `PGPASSWORD`로 전달되므로 URL-unsafe punctuation도 허용한다.
   Compose의 API/Dagster는 production profile을 강제로 사용하므로 admin token이
   없으면 기동하지 않는다.
2. `docker compose -f compose.yaml up -d --build`를 실행한다. `migrate` one-shot
   서비스가 `alembic upgrade head`를 완료한 뒤 API/Dagster가 시작된다.
3. migration 복구가 필요하면 `docker compose -f compose.yaml run --rm migrate`를
   별도로 실행하고, `docker compose ... ps`에서 완료 상태를 확인한다.
4. API/Dagster/DB는 compose 기본값처럼 loopback/internal network에 두고,
   필요한 경우 인증된 gateway 또는 SSH tunnel만 web/API에 연결한다. DB port를
   인터넷에 직접 publish하지 않는다.
5. `/health`, `/version`, admin sync-runs에서 smoke를 확인한다.

현재 migration head는 `0003_sync_run_heartbeat`다. 장시간 KMA 실행은 그룹마다
heartbeat를 갱신하므로, 180분 동안 heartbeat가 없는 running row만 자동으로 failed
회수된다.

Next.js admin은 내부 네트워크에서만 노출한다. 외부 접근이 필요하면
`WEATHER_UI_USER`/`WEATHER_UI_PASSWORD` Basic Auth(또는 조직 SSO)를 reverse
proxy 앞에 두고, `WEATHER_API_INTERNAL_URL`과 `WEATHER_ADMIN_TOKEN`은
server-side 환경변수로만 주입한다. Next proxy가 브라우저에 backend token을
전달하지 않도록 한다.

서비스 key가 없거나 target이 비어 있으면 Dagster run은 실패해야 하며, 기존
immutable facts를 삭제하거나 부분 publish하지 않는다. MVP는 durable cursor 없이
매 실행에서 provider 응답을 재검증한다.
