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
2. `docker compose -f compose.yaml up -d --build`를 실행한다. `migrate` one-shot
   서비스가 `alembic upgrade head`를 완료한 뒤 API/Dagster가 시작된다.
3. migration 복구가 필요하면 `docker compose -f compose.yaml run --rm migrate`를
   별도로 실행하고, `docker compose ... ps`에서 완료 상태를 확인한다.
4. API/Dagster/DB는 compose 기본값처럼 loopback/internal network에 두고,
   필요한 경우 인증된 gateway 또는 SSH tunnel만 web/API에 연결한다. DB port를
   인터넷에 직접 publish하지 않는다.
5. `/health`, `/version`, admin sync-runs에서 smoke를 확인한다. n150 운영 endpoint는
   API `https://weather-api.digitie.mywire.org`, Basic Auth로 보호된 Dagster
   `https://weather-dagster.digitie.mywire.org`, web
   `https://weather.digitie.mywire.org`를 사용한다. 로컬 loopback 포트는 각각
   `14101`, `14102`, `14105`이며 PostgreSQL은 `14100`이다.
   Prometheus는 `14104` loopback에서만 동작한다. `curl -H
   "Authorization: Bearer $KOR_TRAVEL_WEATHER_METRICS_TOKEN"
   http://127.0.0.1:14101/metrics`와 `curl http://127.0.0.1:14104/-/ready`로 scrape를
   확인하고 public gateway에는 14104를 연결하지 않는다.

현재 migration head는 `0008_admin_login_rate_limits`다. 장시간 KMA 실행은 그룹마다
heartbeat를 갱신하므로, 180분 동안 heartbeat가 없는 running row만 자동으로 failed
회수된다.

Next.js admin은 내부 네트워크에서만 노출한다. Dagster 14102는
`dagster-gateway` Basic Auth 뒤에 있으며, web의 server-side GraphQL proxy만
내부 `dagster:14102`에 직접 접근한다. Web UI 자체는 kor-travel-geo와 같이
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
