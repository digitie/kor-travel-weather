# Weather111 독립 적대적 FULL 리뷰 — James r1

- Review ID: WEATHER111-JAMES-R1 / reviewer 실행 ID: /root/review_finish_a.
- 렌즈: Frontend/UI 상태 진실성, 인증·CSRF·재예약, 접근성·모바일, 최종 주소 사용 경계.
- **판정: REQUEST_CHANGES. OPEN P0=0 / P1=0 / P2=2 / P3=0.**
- 두 P2를 수정하고 post-fix 검토하기 전 현 head의 소비자 소스 수용을 PASS로 기록하지 않는다.
- Weather base: 28f731e6dcb12d5f4cde2ad86fd25acbf3da2eac.
- Weather head: c52e08b5a5d7401fc600ddd4a378f327f6395f60.
- Common 검토 제품: 0f768a1974a65bfade4c0fa3c79b897c9663e1a6.
- Manifest: /home/digitie/dev/reverse-address-weather-review-manifest-20261018-r1.json.
- Manifest SHA256: 2c1d031e4aed34a14a88be9a29124963c72b44fc6a8a1c784370aab90d60b469.
- Transport dd66363af1421e9b34448a06afd8fe00fde28489는 문맥 전용이며 정식 판정하지 않는다.
- 시작: 2026-10-18 UTC 본 세션(분·초 사전 계측 없음). 종료는 문서 끝에 기록한다.
- 상대 reviewer의 이번 원문·결과는 열람하지 않았다.

## 기준선·범위·격리

Weather AGENTS를 고정 Git object에서 읽었다. base→head 37파일 1041+/19- 전체 diff를 읽고, BFF/화면·CSS·테스트·query 문서·인증/Origin/session 의존 경로·API/OpenAPI/TS·migration·설정/Compose·Dagster 연결·Common pin을 추적했다. package-lock/uv.lock의 추가·핀 변경과 테스트 설정도 확인했다.

제품 파일은 git show c52e08b:<path>로 읽었다. 시작과 종료 Weather worktree status는 비어 있었다. 소유 임시 디렉터리의 Git-object 복사와 메모리 transpile/compile만 실행했다. 제품·index·branch·원 WIP는 수정하지 않았다.

운영/SSH/Docker, 실제 provider/키, 실제 DB, 브라우저/실제 UI, 실제 Dagster 실행 및 Daemon 시작은 하지 않았다. 인메모리 SQLite/JS 응답 fixture만 사용했다. 보고서 외 영구 신규 산출물은 없다.

Root가 고정 head의 Python/frontend CI SUCCESS 및 loopback/injected-provider UI18을 확인했다는 의뢰 증거는 별도로 인정한다. 본인이 새로 실행한 CI나 실브라우저 검증으로 합산하지 않는다. 아래 UI 반례는 오프라인 실제 함수/TS 분기 fixture이며 브라우저 재현이라고 표현하지 않는다.

## W111-A-P2-01 — 같은 requestId의 동시 재시도가 실행 두 개를 예약한다

상태: **OPEN**.

위치:
- packages/kor-travel-weather-admin/frontend/app/api/address-refresh/route.ts:67–91.
- query 계약: packages/kor-travel-weather-admin/frontend/lib/dagster-scope.ts:196–198.
- 순차 재조회만 검사하는 시험: app/api/address-refresh/route.test.ts:52–59.
- 소비하는 Common lease는 동시에 실행하는 worker를 구분하지만 요청 ID별 실행 이력 중복을 처리하지 않는다.

실패 조건:
1. 인증·같은 Origin을 만족하는 동일 requestId의 POST 두 개가 동시에 도착한다.
2. 두 previous 조회가 모두 빈 결과를 읽고, 두 active 조회도 아직 빈 결과를 읽는다.
3. 각 요청이 launchRun을 별도로 호출한다. 조회와 예약 사이에 원자적 예약/unique requestId 경계가 없다.

본인 재현은 고정 route.ts 전체를 TypeScript transpile하여 Node VM에서 실행했다. NextResponse·session 검증 경계는 소유 fixture이며, Origin과 query 상수는 고정 원문을 사용했다. 두 previous 응답 및 두 active 응답을 Promise barrier로 맞춘 fetch 대역을 넣고 동일 요청 ID의 실제 POST 함수 두 개를 Promise.all로 호출했다.

관측:
    {
      "simultaneous_same_request_id": 2,
      "statuses": [202, 202],
      "launches": 2,
      "sequential_replay_status": 202,
      "negative_statuses": [401, 403, 422],
      "real_network": false
    }

후속 순차 replay는 기존 run을 반환하고 세 번째 launch를 만들지 않았다. 따라서 현재 단위시험이 검사하는 순차 멱등성은 통과하면서 동시 경합만 실패한다. 미인증/교차 Origin/임의 job 필드는 upstream 호출 전에 거절되는 양성 보호도 함께 확인했다.

영향:
- Daemon OFF일 때 두 요청은 둘 다 QUEUED로 남을 수 있다. 아직 worker lease를 얻는 단계도 아니다.
- 이후 직렬 실행되어도 첫 수동 cycle이 끝난 다음 두 번째 force 실행은 새 cycle을 시작한다.
- 이를 Common0f768a1 실제 함수 + 인메모리 SQLite + 주소 1개로 별도 확인했다. force 실행을 직렬 두 번 호출하면 status가 둘 다 success, provider 호출 2회, refresh_id는 서로 달랐다.
- 따라서 DB lease는 동시 provider 실행 격리 수단이지 같은 관리자 요청의 예약 중복 방지 수단이 아니다. 비용/쿼터를 반복 사용하고 관리자에게 동일 요청으로 run 두 개를 만들 수 있다. 실제 운영 중복이 발생했다는 주장은 아니다.

권고:
requestId의 최초 예약과 재조회를 원자적·서버 소유 방식으로 결박한다. 여러 BFF worker/replica에도 효력이 있는 경계를 선택하고, upstream이 지원하지 않는 외부 runId 주입을 억지로 추가하지 않는다(현재 schema 시험은 ExecutionMetadata에 runId가 없음을 명시한다). 같은 UUID 동시 요청 두 개와 “첫 응답을 잃은 뒤 겹친 재시도”를 시험하여 launch1 및 동일 run 반환을 확인한다. 단순 동시 lease만으로 고쳤다고 닫지 않는다.

## W111-A-P2-02 — 이미 실패한 이전 실행 재조회도 새 예약 성공으로 알린다

상태: **OPEN**.

위치:
- app/api/address-refresh/route.ts:71–72: 이전 결과의 실제 status를 그대로 202로 반환한다.
- components/address-refresh.tsx:42–46: body.status를 전혀 읽지 않고 항상 “실행을 예약했습니다”를 표시한다.
- :64의 “예약한 실행 보기” 링크도 이전 run을 가리킨다.

실패 조건:
첫 예약의 응답을 브라우저가 잃어 requestId가 유지됐고, 그 실행은 이후 FAILURE/CANCELED/SUCCESS로 끝났다. 사용자가 같은 요청으로 재조회하면 BFF가 올바르게 기존 실행을 찾아 새 launch 없이 반환한다. 그러나 화면은 “이전 요청 상태를 확인했다”가 아니라 새 실행이 예약되었다고 알린다. 실패한 실행의 재실행이 예약됐다고 오해할 수 있다.

본인 재현:
- 고정 AddressRefresh TSX를 transpile했다.
- 소유 useState/useRef/useCallback/useEffect/JSX stub으로 실제 컴포넌트 함수·launch 분기를 실행했다(실제 React DOM/브라우저 아님).
- GET은 configured=true 요약, POST는 정상적인 replay DTO {runId:"previous-failed-run",status:"FAILURE"}와 202를 반환했다.
- 수동 버튼 경로를 실행하고 재렌더된 출력 구조를 확인했다.

관측:
    {
      "upstream_replayed_status": "FAILURE",
      "notice_claims_reserved": true,
      "rendered_failure_status": false,
      "link_points_to_previous_run": true,
      "real_react_browser": false,
      "network": false
    }

route의 기존 완료 run 반환 단위시험과 결합하면 도달 가능한 정상 응답이다. 링크로 이동하면 나중에 실제 실패를 확인할 수 있다는 점이 현재 성공 notice를 사실로 만들지는 않는다. 새 queued 예약과 이미 종료된 이전 요청 확인은 서로 다른 상태다.

권고:
반환 run status를 사용해 QUEUED/진행/완료/실패·취소를 구분한다. replay 여부를 명시하는 최소 서버 DTO가 필요하다면 새 예약과 이전 상태 확인을 함께 구분한다. 같은 requestId는 새 실행을 만들지 않는 기존 계약을 유지하면서, 실패한 요청을 새 의도로 재예약할 수 있는 안내/행동도 명시한다. 이전 FAILURE replay를 받았을 때 성공 예약 notice를 띄우지 않는 실제 컴포넌트 회귀가 필요하다.

## 나머지 신뢰 경계·최종 주소 사용 확인

인증/CSRF:
- 새 BFF는 서명 세션 검증과 durable session active를 모두 요구한다.
- POST는 exact configured Origin을 확인하고 requestId 한 필드의 UUID JSON만 받는다.
- job/location/repository/run config는 서버 상수이며 브라우저 임의 GraphQL·job을 전달하지 않는다.
- API 상태 endpoint는 require_admin이고 BFF가 서버 관리자 token을 붙인다. provider 키를 브라우저 응답으로 보내지 않는다.
- provider/GraphQL 원문 예외는 정적 사용자 오류로 정리한다. 새로운 임의 job 실행/외부 redirect/키 출력 경로는 발견하지 못했다.
- 다만 위 경합/상태 지적은 이 인증 경계 안의 정상 관리자 재시도 문제다.

주소 사용·원천 보존:
- 활성 weather_locations만 Common DatabaseAddressSource로 읽고, 별도 entries/control에 파생 결과를 쓴다.
- 원천 region_code, nx/ny, 좌표, 관리자 metadata, 과거 fact를 새 주소로 UPDATE하는 연결은 없다.
- 현 최종 주소의 공개 사용처는 GET /v1/admin/address-refresh의 최근 최대50개 요약과 Admin Dagster 표다. 이 표는 address.full을 표시하고 stale/error를 함께 보인다.
- 일반 locations/weather fact DTO나 Map의 주소를 이 값으로 자동 치환한 구현은 아니다. “전체 일반 화면의 주소가 모두 새 값으로 교체된다”는 완료 주장으로 확대하면 안 된다. 현재 manifest/가이드의 “파생 결과 별도 표시·원천 보존”과는 일치한다.
- VWorld 우선·Kakao 보조는 고정 Common0f768a1에 위임하며 별도 provider 재구현은 없다. 위치별 달력6개월과 수동 force, 키 없을 때 실행 버튼 비활성화가 연결돼 있다.
- 표는 실패/기한 경과 주소를 최신으로 보장하지 않는다고 설명한다. 정상 무결과의 보수적 stale 표현은 Common 기존 검토와 동일하며 이 보고서에서 추가 소비자 defect로 중복 계상하지 않았다.

접근성·모바일:
- section의 접근 가능한 이름, 제목, button 텍스트, 오류 role=alert, 안내 role=status, table caption/헤더가 있다.
- 표는 min-width640+overflow-x:auto 영역에 묶이고, 영역에 이름과 tabIndex0이 있어 키보드 스크롤 진입이 가능하다.
- 실제 스크린리더/모바일/브라우저는 본인 NOT_RUN이다. root의 1365/390px UI18 결과와 본인의 정적 확인을 구분한다.

Daemon/자동 재개:
- 등록한 sensor가 실제 Daemon을 start/restore하는 코드는 없다. Daemon OFF에서 수동 예약이 대기한다는 UI 설명은 맞다.
- 주소 provider 호출은 job에 있고 pending sensor/상태 API는 조회 경계다.
- 실제 운영 migration/예약/센서/Daemon을 실행하지 않았다.

## 독립 검증 결과

| 실행 | 결과 | 범위 |
| --- | --- | --- |
| Manifest SHA, Weather exact commit, 시작·종료 status | 일치 / clean | immutable object 기준 |
| 고정 route.test.ts 독립 Vitest 실행 | **5 PASS**, 339ms 전체/시험21ms | 소유 임시 Git-object 복사, test cache 비활성화/캐시 경로도 임시, 실제 fetch 대역 |
| 실제 POST 함수 동시 동일 UUID + 순차 replay + auth/CSRF/body 음성 | 경합 launch2 반례, 나머지 음성 거절 | 실제 네트워크0, Next/session은 fixture |
| 고정 TSX 실제 launch 분기의 FAILURE replay | 잘못된 예약 notice 반례 | hook/JSX stub, 실제 DOM 아님 |
| Common 직렬 force2 | 호출2·서로 다른 cycle 확인 | 인메모리 SQLite, 실제 Dagster 실행0 |
| migration0021 선언 ↔ Common 테이블 | 컬럼/type/PK/nullability/timezone 전부 일치, index3 | op recorder, 실제 DDL/DB0 |
| API Pydantic 요약 ↔ OpenAPI | 필수10필드 동일, endpoint 존재 | AST/JSON 정적 비교 |

공통 회귀·Python657·frontend207·type/build·CI SUCCESS·root loopback18은 의뢰 manifest의 외부 증거다. 본인이 재실행한 수치로 표에 합치지 않았다.

## 미검증·남은 범위

실제 provider/credentials, 실제 PG migration·경합, 실제 Dagster GraphQL 저장소, 실제 인증/브라우저/E2E/배포는 NOT_RUN이다. Common은 이전 James r2에서 고정0f768a1 소스 PASS였으나 이번 소비자 예약 경합/표시 판단을 대신하지 않는다. Transport 정식 CI/소비자 판정은 제외한다.

두 P2는 실행 가능한 소유 반례와 위치를 위에 고정했다. 수정 소스는 만들지 않았으며 새 immutable head에서 원 반례 및 전체 delta를 post-fix 검토해야 한다. 원 실패 증거를 삭제하거나 사용자 daemon OFF 조건을 자동 복구로 바꾸면 안 된다.

종료 UTC: 2026-10-18T01:57:48.839997+00:00
