# Admin UI boundary

weather admin은 `kor-travel-map` admin의 운영 workbench 패턴을 공유하되, 지도
feature 대신 weather location과 provider run을 중심으로 구성한다.

## Information architecture

| Group | Route | Purpose |
| --- | --- | --- |
| 개요 | `/` | API health, catalog 수, 최근 run 요약 |
| Weather source | `/weather` | 지도/목록 전환, 위치 선택, latest·forecast inspector |
| Weather source | `/locations` | enabled location catalog 등록·비활성화 |
| Weather source | `/datasets` | provider/dataset 계약과 credential configured 상태 |
| 운영 | `/sync-runs` | immutable source lineage와 publish counter |
| 운영 | `/admin/dagster` | repository/job/schedule/recent run 관찰 및 원본 UI 링크 |
| 운영 | `/api-test` | server-side proxy를 통한 API 요청/Problem 응답 확인 |

## UX contract

- 좌측 rail은 데스크톱에서 고정되고 62rem 이하에서는 map admin과 같이 가로
  navigation으로 바뀐다. 320px 폭에서도 버튼·입력은 44px touch target을 유지한다.
- `/weather`는 지도와 inspector를 동시에 제공하고 `Map/List` 탭으로 목록을
  지도 위 overlay로 전환한다. 지도는 OSM raster와 attribution을 사용하며 위치
  데이터는 public enabled catalog만 읽는다.
- 모든 비동기 panel은 loading, empty, error 상태를 같은 위치에 표시한다. latest
  projection과 forecast history는 서로 다른 섹션으로 구분한다.
- 로그인·로그아웃을 포함한 상태 변경 요청은 Docker Manager와 동일하게
  등록된 same-origin Origin 검증과 HttpOnly signed session을 거친다. 로그인 응답은
  검증된 `username`과 안전한 local `next`를 반환하고 세션 cookie는 `SameSite=Strict`다.
  Next server proxy가 `x-admin-token`을 주입하며 browser/client bundle에는 token을
  포함하지 않는다. Basic Auth는 reverse proxy 호환 fallback이다.
- `/admin/dagster`의 GraphQL은 server-side internal URL로만 전달한다. 운영 외부
  링크는 `NEXT_PUBLIC_DAGSTER_URL`로 명시하고, 상태 확인 실패도 UI에 error
  banner로 남긴다.

## Configuration

로컬 frontend `.env.local`에는 `WEATHER_API_INTERNAL_URL`,
`DAGSTER_UI_INTERNAL_URL`, `WEATHER_ADMIN_TOKEN`, `WEATHER_UI_USER`,
`WEATHER_UI_PASSWORD`, `WEATHER_UI_SESSION_SECRET`를 설정한다. Compose에서는
`DAGSTER_UI_INTERNAL_URL=http://dagster:14102`를 web 컨테이너에 주입한다. 세션
서명키와 backend token은 저장소/브라우저 로그에 기록하지 않는다. production 세션
서명키는 최소 32바이트의 무작위 값이어야 하며, 예제의 placeholder는 거부된다.
`WEATHER_UI_TRUST_PROXY=true`는 reverse proxy가 `X-Forwarded-For`를 신뢰된 마지막
hop으로 다시 쓰거나 append하는 경우에만 설정한다(기본값은 신뢰하지 않음). Next.js 실행 환경에서 소켓 IP가
제공되지 않고 이 옵션도 꺼져 있으면 로그인 제한은 전역 `unknown` 버킷을 만들지
않는다. 운영에서는 HAProxy 등 신뢰된 경계에서 클라이언트 IP를 덮어써 전달하고
이 옵션을 켜며, gateway 자체에도 로그인 rate-limit을 둔다.
production 상태 변경 요청은 `WEATHER_UI_PUBLIC_ORIGIN` 또는
`WEATHER_UI_PUBLIC_ORIGINS`에 등록된 Origin만 허용한다. 이 값을 비워 두면
CSRF 검사는 fail-closed로 동작하며, 요청자가 보낸 `X-Forwarded-Host`/`X-Forwarded-Proto`는
Origin 검증에 사용하지 않는다. 로그아웃한 세션 cookie는 서버 revocation store에서
즉시 폐기된다. revocation marker는 PostgreSQL에 저장되므로 web 프로세스 재시작이나
다중 replica에서도 같은 cookie가 다시 승인되지 않는다.

Provider API key override는 `/settings/providers`에서 관리한다. API와 Dagster가
공유하는 `KOR_TRAVEL_WEATHER_CREDENTIAL_ENCRYPTION_KEY`(Fernet URL-safe key)가
설정된 경우에만 키를 암호화해 저장하며, 화면에는 source·fingerprint·마지막 4자리만
표시한다. 환경변수로 주입된 키는 이 화면에서 삭제할 수 없다.
