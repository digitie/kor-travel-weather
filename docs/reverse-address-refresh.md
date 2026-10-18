# 6개월 자동 주소 갱신과 Admin 수동 실행

정본 구현 계약은 [Common 구현 가이드](https://github.com/digitie/kor-travel-common/blob/812b291659e40564595c3c28b7e8d51cbcc46df9/docs/reverse-address-refresh.md)를 따른다.
기존 VWorld 우선·Kakao 보조를 유지하며 Geo API나 데이터 토큰에는 의존하지 않는다.

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

로컬 실브라우저 검증: Chromium 1365×900·390×844에서 9개씩 통과했다.
실제 로그인·상태 API·Dagster 큐 예약·동일 요청 재조회·중복 예약 차단·CSRF를 확인했다.
실패 주소·키 미설정·503 복구는 browser route fixture로 검사했다. 제공자 응답은
주입한 fixture이며 VWorld·Kakao 운영 API를 실호출한 결과로 해석하지 않는다.
테스트용 Daemon도 실행하지 않았고 큐 대기 상태를 확인했다.

실행: 전용 loopback fixture 환경에서 ADDRESS_REFRESH_UI_E2E=1,
E2E_BASE_URL, E2E_UI_USER, E2E_UI_PASSWORD를 지정하고
npx playwright test e2e/address-refresh.spec.ts --workers=1.
운영 주소에 실수로 예약하지 않도록 spec은 loopback만 허용한다.

공통 Python 전체 495개, 각 Admin Vitest 207개 및 운영 빌드가 통과했다.
Weather 전체 Python 657개와 실제 고정 Dagster GraphQL schema 검증도 통과했다.
Transport 전체 Python 회귀는 실행 중이다(첫 순차 검증은 600초 시간 예산 초과로 미완료).
운영 migration·실제 API 실호출·배포·외부 운영 UI E2E는 미실행이다.
CI·두 독립 리뷰의 최종 결과는 PR 검증 기록에 추가한다.
