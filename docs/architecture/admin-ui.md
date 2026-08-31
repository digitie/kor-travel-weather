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
- 상태 변경 요청은 same-origin Origin 검증과 HttpOnly signed session을 거친다.
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
서명키와 backend token은 저장소/브라우저 로그에 기록하지 않는다.
