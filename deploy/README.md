# Docker 배포 표면

`compose.yaml`은 PostgreSQL과 세 실행 표면을 고정된 포트로 묶는다. 기본 host
binding은 모두 `127.0.0.1`이다. n150처럼 gateway가 Docker 호스트 외부에서 실행되는
환경은 `deploy/compose.n150.yaml` override를 함께 사용해 API/Dagster/web만 LAN
주소에 바인딩한다. PostgreSQL은 항상 loopback에 둔다.

| 서비스 | 포트 | 역할 |
| --- | ---: | --- |
| `db` | 14100 | PostgreSQL 16 |
| `api` | 14101 | FastAPI + Alembic |
| `dagster-gateway` | 14102 | Basic Auth로 보호된 Dagster webserver gateway |
| `web` | 14105 | Next.js admin |

실행 전 compose가 읽는 환경 파일에 `POSTGRES_PASSWORD`,
`KOR_TRAVEL_WEATHER_ADMIN_TOKEN`, `WEATHER_UI_PASSWORD`,
`KOR_TRAVEL_WEATHER_CREDENTIAL_ENCRYPTION_KEY`를 설정한다. root `.env`를
사용해도 되지만 이 변수들은 compose 전용이며 애플리케이션 설정에서는 무시된다.
Compose의 API/Dagster 컨테이너는 `KOR_TRAVEL_WEATHER_ENV=production`을 고정해
`.env.example`의 개발 프로파일이 인증을 우회하지 않도록 한다.
`KOR_TRAVEL_WEATHER_CREDENTIAL_ENCRYPTION_KEY`는 API 키 설정 화면의 DB override를
암호화하는 Fernet URL-safe key이며 API와 Dagster에 동일하게 전달해야 한다. 이 값이
없으면 환경변수 credential만 사용할 수 있고 설정 화면 저장은 503으로 거부된다.
KMA live
수집을 사용할 때만 `KOR_TRAVEL_WEATHER_DATA_GO_KR_SERVICE_KEY`와 JSON target을
추가한다. PostgreSQL 비밀번호는 `PGPASSWORD`로 컨테이너에 전달하므로 `@`, `:` 같은
문자가 포함되어도 DSN URL 파싱을 깨뜨리지 않는다.

```bash
docker compose -f compose.yaml up -d --build
docker compose -f compose.yaml ps
curl http://127.0.0.1:14101/health
open http://127.0.0.1:14105
```

n150 배포는 다음처럼 gateway가 접근할 LAN 주소를 명시한다. LAN 방화벽에서는
HAProxy 호스트에서 오는 `14101`, `14102`, `14105`만 허용한다.

```bash
docker compose --env-file .env \
  -f compose.yaml -f deploy/compose.n150.yaml up -d --build
docker compose --env-file .env \
  -f compose.yaml -f deploy/compose.n150.yaml ps
curl http://192.168.1.14:14101/health
curl -u admin:'<WEATHER_UI_PASSWORD>' http://192.168.1.14:14102/server_info
curl -u admin:'<WEATHER_UI_PASSWORD>' http://192.168.1.14:14105/
```

n150 운영에서는 다음 HTTPS 도메인을 reverse proxy의 정본으로 사용한다.

| 서비스 | 운영 URL |
| --- | --- |
| API | `https://weather-api.digitie.mywire.org` |
| Dagster | `https://weather-dagster.digitie.mywire.org` |
| admin web | `https://weather.digitie.mywire.org` |

admin web Basic Auth 운영 계정 아이디는 `admin`이며 비밀번호는 배포 secret 파일로만
주입한다. 비밀번호를 저장소나 로그에 기록하지 않는다.
Dagster 도메인도 같은 gateway Basic Auth로 보호하며, web의 `/admin/dagster` 화면은
인증된 Next server-side proxy를 통해 내부 Dagster service를 조회한다.

`migrate` one-shot 컨테이너가 API/Dagster보다 먼저 `alembic upgrade head`를 한 번
수행하고, 두 서비스는 migration 성공 후에만 시작한다. migration이 실패하거나
기존 볼륨을 복구한 경우에는 `docker compose -f compose.yaml run --rm migrate`를
실행해 상태를 확인한다. `db` 볼륨은 `weather-postgres`에 유지되며,
삭제/초기화는 운영 백업 확인 후에만 수행한다.
