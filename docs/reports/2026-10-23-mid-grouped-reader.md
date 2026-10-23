# 중기예보 육상·기온의 grouped frozen reader

## 범위

`FrozenMidForecastExports`는 inventory에 승인된 `python-kma-api/kma_mid_forecast`의
육상과 기온을 위치별 한 묶음으로 조합한다. 기존 예보 reader의 공식 확장 지점을
사용하며 별도 인증·cursor 형식을 만들지 않는다. router는 mid일 때만 이 reader로
dispatch한다. 기존 수집·fact·이력·OpenAPI 필드와 운영 flag는 변경하지 않는다.

## 불변식

- 전체 raw scope와 generation 참조는 유지한다. 추가 `mid_locations` 인덱스만으로
  같은 위치의 land/temperature를 찾아 두 발표 시각이 같은 경우에만 새 vector를 발급한다.
- 최신 native·source 미확인, 역할 누락·중복·회차 불일치는 새 vector를 만들지 않는다.
  이전 완전 vector가 있으면 stale로 읽고 없으면503이다. cursor도 같은 RR의 canonical
  catalog·inventory·native 건강 및 현재 역할/회차를 다시 확인한다.
- 미래 target 시각 합집합을 UTC 순서로 정렬하고 metric 배열을 null로 맞춘다. SKY의
  한국어 서술 문자열은 보존한다. known_at은 두 원천의 실제 조회 시각 중 늦은 값이며
  publication 시각으로 바꾸지 않는다. 공통 메타의 source_collected_at은 raw vector 최소값이다.
  같은 의미의 재확인은 이전 artifact를 재사용할 수 있다. 파일의 과거 known_at은
  issued~승인 source clock 범위로 검증하고 응답 known_at은 원래 fact clock을 보존한다.
  재확인 시각은 meta.source_collected_at으로 구분한다.
- delta 판단은 두 part의 publication 중 늦은 값이다. 같은 RR에서 받은 기존 publication
  watermark를 next_since로 유지해, 늦게 승인된 한 part를 건너뛰지 않는다.
- cursor의 g는 위치 인덱스이며 p는0이다. 기존 서명·selection/limit 바인딩·TTL410·위조422를
  재사용한다. 페이지는1~5,000위치, 기존4MiB 상한을 유지한다(요구8MiB보다 엄격하다).
- 한 part당 chunk 하나만 cache한다. chunk당5,000 point/8MiB를 넘으면 거절한다.
  위치 ID의 전역 정렬은 약속하지 않으며 land/temp artifact digest와 위치 ID의 안정 순서로
  인덱스를 만든다. 여러 기온 지역이 섞여도 같은 chunk를 이어 읽을 수 있다.
  원본 날짜 이력이나 weather_current_values를 조회하지 않는다.

## 검증 현황과 남은 수용

초기 순수 계약16건과 Ruff가 통과했다. 역할·회차·불확실 source 거부, SKY·null 배열,
실제 원천 clock, 늦은 publication delta, raw scope보다 큰 위치 cursor, 두 chunk cache,
byte/limit/서명/선택 계약을 확인했다. 이 결과는 실제 PG/API 또는 운영 성능 수치가 아니다.
12개 위치가 세 기온 지역에 번갈아 속하는 fixture의 읽기는 land1+temperature3=4회였다.

고립된 `--network none` PostgreSQL16을 새로 만들고 0001~0033 migration을 적용했다.
SDK 모의 HTTP→native intent/cohort→실제 fact capture→성공 head/inventory→reader 경로로
세 위치의 pagination·서명/limit 바인딩, 부분 승인·늦은 승인 delta, 실패 intent·좌표 변경,
회차 불일치·TTL을 검증했다. 이 단계의 4개 PG 시험과 초기15개 순수 시험은
19PASS(24.90초)였다. 원천 HTTP·운영 DB·daemon은 호출하거나 변경하지 않았다.

같은 의미의 재수집 clock 분리, history/current query 금지와 최초 한 역할 미승인을
더한 최종 집중16+5건은21PASS(33.64초)다. 실제 앱의 기존 service route로 인증·최초503·
gzip·ETag304·실패 뒤200/stale·old cursor·selection/limit/서명422·TTL410과 flag OFF/토큰
미설정 거부3건도 PASS(1.41초)다. TestClient 결과를 실제 TCP/UI로 세지 않는다.

이후 추가 PG·두 독립 리뷰·실제 HTTP/UI와 선행 통합까지 아래 증거로 마감했다.
정확 CI와 main 통합은 PR에서 별도로 수용한다.
운영 배포·flag 활성화·daemon 변경·ARM 비용 개선은 아직 완료하지 않았다.

## 최종 수용 증거

- 제품 고정 `ba70683eb1d8f8b582b060295b169a14643ce987`: 승인된 #159의 전체 제공처
  선택·예보 hook, #158의 실제 중기 collector, #161의 공유 읽기 잠금을 통합했다.
  자체 reader와 제한적인 mid route dispatch를 두 독립 리뷰어가 FULL 검토하고
  마지막 통합 delta까지 PASS/OPEN P0·P1·P2=0으로 폐쇄했다.
- 해당 제품의 PG/순수/API 집중·실제 OS 공유 잠금·중기 native 회귀29건은
  PASS(84.49초), 전체 Ruff PASS다. 원래 값 파일을 재사용하는 재승격에서도
  known_at 불변·meta.source_collected_at 진전을 확인했다.
- 동일 고정 git archive에서 새 전용 PG(0001~0033)와 uvicorn을 실행한 중기 실제
  TCP19건 PASS: 권한·최초503·gzip·SKY/null 정렬·304·위치 페이지·위조/limit/since422·
  부분 승인과 old cursor stale·늦은 승인 delta·native 실패 시304 금지·TTL410.
- 동일 API 제품을 별도 실행한 전체 제공처 실제 TCP11건과 production frontend의
  UI11건도 PASS다. frontend tree `2d4043c541e2b25b6fc7368f456b439bda1269a9`의
  모든 tracked blob과 기존 검증된 BUILD_ID를 실행 전후 직접 대조했다.
  UI route mock0·브라우저 오류0, wrapper/모든 command exit0을 확인했다.
- 전용 DB와 API/frontend 프로세스·브라우저 컨테이너, 별도 중기 시험용 PostgreSQL
  컨테이너까지 정리했다. 원천 HTTP는 SDK 모의 응답이며 실제 원천·운영 API·daemon을
  변경하거나 운영 ARM 비용 개선율을 측정한 결과가 아니다.
- 정확 제품 CI는 PR의 run `39969236234`에서 마감한다. 외부 수용 영수증은 작업
  audit의 `weather160-mid-tcp-report.json`과 `weather160-ba-final-*.json`이며,
  이 문서만의 후속 변경은 승인 제품과의 tree 동일성을 별도로 확인한다.
