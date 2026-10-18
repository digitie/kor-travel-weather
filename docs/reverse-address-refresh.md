# 6개월 자동 주소 갱신과 Admin 수동 실행

정본 구현 계약은 [Common 구현 가이드](https://github.com/digitie/kor-travel-common/blob/522525aed3db934767c1c9e742b3bfc68e91679c/docs/reverse-address-refresh.md)를 따른다.
Transport와 같은 VWorld 우선·Kakao 보조를 적용하며 Geo API나 데이터 토큰에는 의존하지 않는다.

환경변수: KOR_TRAVEL_WEATHER_VWORLD_API_KEY, KOR_TRAVEL_WEATHER_KAKAO_REST_API_KEY,
KOR_TRAVEL_WEATHER_ADDRESS_REFRESH_AUTO_ENABLED(기본 true),
KOR_TRAVEL_WEATHER_ADDRESS_REFRESH_BATCH_SIZE(기본 50),
KOR_TRAVEL_WEATHER_ADDRESS_REFRESH_TIMEOUT_SECONDS(기본 5).
대상은 활성 weather_locations다. KMA 격자·region_code·source lineage를 변경하지 않는다.
Admin 상태 API는 GET /v1/admin/address-refresh다. 소비자 migration은 0021_reverse_addresses다.

Admin의 Dagster 운영 화면에서 전체 주소 수동 갱신을 누른다. 서버에서 고정한
address_refresh_job에 force=true를 예약하며, 큐 등록과 완료를 구별한다.
확인 주소는 별도 파생 결과로 표시하고 원천 주소를 덮어쓰지 않는다.
주소별 마지막 확인일부터 달력 6개월마다 갱신한다. 자동 due 검사는 10분 주기이나
대상이 없을 때 provider를 호출하거나 run을 생성하지 않는다.
실패는 마지막 정상 주소·확인일을 보존하고 재시도한다. 중단은 영속 커서로 재개한다.
자동 갱신을 꺼도 이미 시작한 수동 배치는 계속 이어 처리한다.
Daemon은 기존 중지 상태를 유지하며 이 변경이 운영을 임의로 재개하지 않는다.

## 검증 기록

로컬 실브라우저 검증: Chromium 1365×900·390×844를 합해 각 앱 13개가 통과했다.
실제 로그인·상태 API·Dagster 큐 예약·동일 요청 재조회·중복 예약 차단·CSRF를 확인했다.
실패 주소·키 미설정·503 복구는 browser route fixture로 검사했다. 제공자 응답은
주입한 fixture이며 VWorld·Kakao 운영 API를 실호출한 결과로 해석하지 않는다.
테스트용 Daemon도 실행하지 않았고 큐 대기 상태를 확인했다.

실행: 전용 loopback fixture 환경에서 ADDRESS_REFRESH_UI_E2E=1,
E2E_BASE_URL, E2E_UI_USER, E2E_UI_PASSWORD를 지정하고
npx playwright test e2e/address-refresh.spec.ts --workers=1.
운영 주소에 실수로 예약하지 않도록 spec은 loopback만 허용한다.

공통 Python 전체 509개, 각 Admin Vitest 211개 및 운영 빌드가 통과했다.
Weather 전체 Python 657개와 실제 고정 Dagster GraphQL schema 검증도 통과했다.
Transport 전체 Python 회귀는 독립 SQLite 4 worker에서 838개 통과·환경 조건 10개 skip이다.
첫 순차 검증은 600초 시간 예산 초과로 미완료였고, 뒤의 전체 병렬 검증으로 완료했다.
PC·모바일의 표 좌우 스크롤과 키보드 focus도 추가 확인했다.
운영 migration·실제 API 실호출·배포·외부 운영 UI E2E는 미실행이다.
CI·두 독립 리뷰의 최종 결과는 PR 검증 기록에 추가한다.

## 독립 리뷰 반영

Common의 두 독립 리뷰에서 HTTPX 정상 요청의 가짜 키/URL 로그 반례, 완료 후 만료 owner 표시,
정상 무결과 fallback 분류 및 동일 좌표 무결과 중복 호출을 재현하고 공통 엔진에서 수정했다.
추가 자체 반례였던 배치 1의 실패 재시도 쏠림도 보완했다. 원본 결과·재검증은 Common PR에 보존한다.
Weather와 Transport 모두 같은 VWorld 우선·Kakao fallback 구현을 사용한다.

## 추가 실패 경계 보강과 한계

자동 달력 갱신이 꺼져도 이미 시작한 수동 cycle의 실패는 backoff 후 이어 처리한다.
backoff 대기 행만 있을 때는 run을 만들지 않는다. 실제 갱신 대상을 SQL에서 선택하고,
커서 앞의 신규 위치·좌표 변경·6개월 due를 순환 처리한다. 비활성/삭제 위치의 오류가
활성 재시도 슬롯을 차지하지 않으며 동일 cycle의 만료 캐시도 재확인한다.

BFF의 단일 프로세스 진입 제한에 더해 Common webserver의 launchIdempotentRun을 사용한다.
요청 UUID를 소문자로 정규화하며, 동일 요청의 native 실행을 재사용한다. 생성 후 제출 전
NOT_STARTED 상태로 중단된 실행은 같은 ID로 제출을 복구한다. 해당 mutation을 제공하지
않는 기존 webserver에서는 502로 실패하며 무작위 실행 ID의 기존 mutation으로 우회하지 않는다.
Common webserver를 먼저 배포한 다음 Admin UI를 배포해야 한다.
요청 tag로 이전 실행을 재조회하고 실패·취소·완료를 새 예약과 구분해 표시한다.
확인 주소는 이 Admin/API의 파생 결과이며 일반 locations/지도 응답의 원천 필드를 치환하지 않는다.

추가 루프백 UI 26개는 기존 실행의 실패·취소·완료 표시까지 포함해 통과했다.
최초 재기동 검증에서 Weather 2 PASS/9 FAIL은 전용 실행 환경의 API/Dagster PYTHONPATH 누락이었다.
제품을 바꾸지 않고 전용 실행 script를 보정한 후 두 앱 각각 11 PASS로 재검증했다.
제공자는 fixture이며 운영 제공자·배포·운영 UI 검증이 아니다.

Transport의 소유 Docker 전체 회귀는 dd66363 기준 835 PASS/13 SKIP다.
기본 SQLite/bench 조건 10개와 앱 이미지에 Docker CLI가 없어 Compose 렌더 3개가 추가 skip됐다.
같은 3개 렌더는 호스트 전체 회귀에서 통과했다. 별도 Docker Admin 207개와 관련 Python 19개도 통과했다.
첫 Docker 시도 36 FAIL/207 PASS/1 SKIP/4 collection ERROR와 다음 3 FAIL/255 PASS/1 SKIP은
모노레포 경로·Node·시험용 PATH의 python3 누락이었다. 소유 fixture에만 원본 archive·도구를 제공해
전체 회귀를 통과시켰고 제품 Dockerfile을 시험 편의를 위해 변경하지 않았다.
최종 Common pin 이후의 집중 계약/CI는 별도로 확인한다.

늦은 상태 GET200이 POST의 실행 실패를 지우는 경합을 두 앱의 두 화면 크기에서 재현했다(4 FAIL).
조회 오류와 실행 오류 상태를 분리한 뒤 같은 반례를 포함한 루프백 UI26개가 통과했다.
최종 공통 c067 pin에서 Weather CI Python658·Admin211, Transport Docker Python19·Admin211도 통과했다.
Common PR39는 두 독립 FULL source 리뷰·최종 CI14개 통과 후 merge됐으며 소비자 최종 리뷰는 별도다.

Weather의 최종 두 독립 리뷰·CI와 PR111 merge 근거는 [최종 closure](reviews/2026-10-18-address-refresh-final.md)에 보존했다. 이 후속 문서는 제품·스키마·배포를 바꾸지 않는다.

## 현재 원천의 최근 정상 확인 시각 보정

Common40의 522525a pin은 현재 원천 SELECT에 있고 좌표가 같은 파생 결과에서만
최근 정상 확인 시각을 집계한다. 비활성·제외·이동 전 결과는 현재 확인 시각에 섞지 않고
과거 행은 보존한다. 같은 좌표의 재시도 실패에는 마지막 정상 확인 시각을 유지한다.
이 pin의 검증·CI·두 독립 리뷰와 운영 수용은 이전 기능 검증과 구분한다.

Common40·Weather113은 두 FULL SOURCE PASS/최종 CI 후 머지했다. 새 pin의 전체661·UI13 및
[독립 원문과 운영 미실행 범위](reviews/2026-10-18-address-summary-final.md)를 확인한다.

## 시간대·응답 유실 후 재시도 회귀

확인일·다음 갱신일은 브라우저 시간대와 관계없이 Asia/Seoul로 표시한다.
UTC·미국 서부 시간대 PC/모바일 4건을 기존 코드에서 실패 재현한 뒤 통과시켰다.

소유 loopback 프록시가 첫 mutation을 12초 늦게 전달하도록 하고, BFF의 10초 timeout 이후
재시도했다. 기존 코드에서는 실제 native Dagster QUEUED 실행이 같은 요청에 두 개 생겼다.
Common41 초안 23e4775와 새 BFF에서는 동일 시험에서 실행 하나가 유지됐고,
각 앱의 기존 UI 회귀 17건도 통과했다. 운영 제공자 API와 daemon은 실행하지 않았다.

단, 23e4775는 독립 리뷰에서 동시 NOT_STARTED 제출의 상태 역행 문제가 발견되어
최종 수용본이 아니다. 제출 보호 수정·독립 재리뷰·최종 pin 검증 및 운영 배포가 남아 있다.
위 지연 시험 통과를 worker 중복 실행 방지 전체의 증거로 해석하지 않는다.

전용 fixture에서 ADDRESS_REFRESH_NATIVE_DELAY_E2E=1을 설정하고
npx playwright test e2e/address-refresh-native-retry.spec.ts --workers=1로 재현한다.
loopback 24602 프록시는 첫 요청을 지연시켜 24102 native GraphQL로 전달하며,
두 앱은 같은 프록시를 쓰므로 이 지연 시험은 순차 실행한다.

프록시는 Admin frontend 디렉터리에서 node e2e/fixtures/native-delay-proxy.mjs로 실행한다.
시험용 BFF의 Dagster 내부 URL을 http://127.0.0.1:24602로 지정하고 실제 native webserver는
24102에서 실행한다. 기존 fixture의 수동 큐를 먼저 정리한 격리 환경에서만 실행하며,
끝난 뒤 생성한 큐·프록시·시험 서버를 정리한다. 운영 daemon을 기동하는 절차가 아니다.
