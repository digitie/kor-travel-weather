# Pinvi637: 명시적 기상특보 이벤트 계약

## 문제와 범위

활성 특보 카드 API는 해제 이벤트를 제거한다. 소비자가 빈 목록에서 해제를 추정하면 조회 실패·만료·부분 조회를 오보로 바꾼다. 공개 read 경계에 원천 이벤트·발령/해제 의미·적용 지역의 확인 상태를 추가한다. 기존 활성 카드 응답 의미는 유지한다.

발령/해제를 연결하는 정체성과 지역은 owner가 정의한다. 실제 python-kma-api 공개 응답과 Weather 수집 경계를 먼저 확인한다. 원천이 제공하지 않는 지역을 만들어 내지 않으며, 발행 관서 그룹 fan-out을 검증된 영향 지역이라고 표시하지 않는다. 필요하면 원천 상세 조회를 보완한다.

## 검증과 진행

초안 PR과 기능별 원격 커밋, WSL 실제 DB/HTTP·fixture 검증, 독립 적대 리뷰2인, 정확 최종HEAD의 적용 CI 확인 후 일반 squash merge한다. API DTO/OpenAPI와 TypeScript 타입을 함께 갱신한다. 사용자 최신 지시에 따라 운영 접근·배포·테스트는 하지 않는다. Pinvi 소비자는 owner 병합 계약을 동기화한 뒤 구현한다.

## 공식 원천 확인과 구현 결정

- Pinvi636/638은 PR688/5602cf3으로 병합했다. 최종 a5c0abc의 적용 CI17성공, 독립 리뷰2인 무차단, WSL 실제 UI8통과이며 운영 접근은 없었다. 이제 이 선행 owner 작업을 진행한다.
- 공식 data.go.kr 15000415의 260601 ZIP(SHA256 6cb5f8e52df4709d5e7daff129948a33620bf708b9f9abd405480e07b0b32ca4)을 확인했다. 공개 SDK request_with_metadata로 WthrWrnInfoService/getPwnCd를 호출하며 stnId108(전국)을 명시한다. 다른 APIHub 인증은 추가하지 않는다.
- command1/2/3/6/7/8은 발령/해제/연장/정정/변경발표/변경해제로 구분하고 cancel1은 취소로 보존한다. 최신 warnVar13(열대야), warnStress2(중대경보)도 포함한다.
- 원천에는 발령·해제를 연결하는 회차 ID가 없다. 발표의 지역·종류·강도·시각·일련번호와 의미로 안정적인 개별 이벤트를 식별한다. occurrence_id는 null/미확정이며 근접한 과거 발표로 회차를 추정하지 않는다. 소비자 중복 제어는 여행·공식 발표별로 정의한다.
- 신규 정규화 이벤트 테이블1개만 추가하고 기존 SourceRecord/SyncRun/run-source를 재사용한다. 모든 bounded 페이지를 받은 뒤 같은 transaction에서 canonical 원천 key·이벤트·성공 종결을 게시한다. 회수된 run은 게시하지 못한다. 실패·누락·오래된 자료를 해제로 변환하지 않는다.
- 공식 XLSX의 REG_UP·유효기간과 primary 기상청 안내, 보존된 공식202605 Juso의 법정동 코드를 대조한다. 시도·시군구 전체가 확인된 구역만 연결하고, 산지·제외·부분 구역은 unknown이다. prefix로 특보구역의 부모를 추정하지 않는다.
- 공개 조회는 법정동 namespace를 명시하며 현재 카드 API와 분리한다. 조회 시각·성공/실패·원천 페이지 완결성·부분 매핑을 드러낸다. 이벤트는31일 보존하며 지원 이력 밖 요청은 정상적인 빈 결과로 취급하지 않는다.

공식 근거: https://www.data.go.kr/data/15000415/openapi.do ,
https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId=FILE_000000003648415&fileDetailSn=1 ,
https://www.data.go.kr/data/15043573/fileData.do ,
https://www.weather.go.kr/w/forecast/guide/wrn-area.do .
원본 자료는 workspace 밖의 소유 캐시에 보존하며 원본 dataset은 편집하거나 커밋하지 않는다.

## 원천 의미·지역 매핑 체크포인트

공식 command·취소·회차 미확정 정규화와 전체 행정구역 매핑을 추가했다. 원본 CSV·XLSX·기상청 HTML·Juso ZIP을 읽기만 하는 재생성 스크립트와 원천 SHA256을 함께 보존한다. 공식 REG_UP 상위 관계로 광주·고성 동명이역을 구분하며, 제외·산지·평지·모호한 안내는 전체 시군구로 넓히지 않는다. 현재413구역 중178개는 전체 행정구역 근거가 확인됐고 나머지는 unknown이다. 이 수치는 전국 지역 커버리지 비율이 아니다.

WSL 소유 Postgres에서 정규화19건과 매핑6건, 총25건이 통과했다. 기본 pytest autouse가 DB를 요구하므로 순수 정규화 시험에도 소유 PG를 사용했다. 처음 WSL idle 종료로 시험 컨테이너가 사라진 환경 오류는 제품 실패와 구분하고, 작업 중 전용 WSL shell을 유지하여 재실행했다. 아직 신규 저장·수집·공개 API·전체 검증·최종 리뷰는 남아 있다.

## 중간 적대 리뷰 보완·원자적 저장 체크포인트

두 전문 리뷰가 중복 지적한 10자리 BJD의 SGG prefix만 확인하던 문제와, 현재 매핑에 코드의 과거 등록기간을 붙이던 문제를 실제 실패2건으로 고정했다. 이제 개별 10자리 코드는 원천의 동·리 suffix 집합과 대조하고, 2/5자리 집계 및 10자리 zero-padding 집계는 명시적으로 구분한다. 코드 등록기간과 매핑 확인 이후의 적용기간도 분리했다. 확인 전 과거 시점은 unknown이다.

신규 이벤트 테이블과 원천 FK·불변 trigger·조회 인덱스, 기존 실행·원천의 같은 transaction 게시, 회수된 실행의 게시 차단을 추가했다. 동일 원천의 이전 canonical key를 재사용하고, 원천 row에서 직접 정규화하여 별도 가짜 이벤트 입력 경계를 만들지 않는다. 31일 이전 이벤트 정리와 원천 GC의 새 참조 보호도 연결했다.

보완 후 정규화·매핑·실제 PG 저장/권한/보존 검증41건이 통과했다. 공개 API·수집 job과 최종 전체 검증·리뷰는 아직 남아 있다.

## 수집·공개 계약 체크포인트

별도 getPwnCd 수집 job/5분 schedule과 공개 alert-events API, OpenAPI export·TypeScript 타입/조회 helper를 연결했다. source row에서 정규화한 관측을 페이지 검증 후에만 게시한다. 최신 실패·정상 빈 목록·부분 지역 매핑·확인 전 관측·보존 범위·조회 상한을 구분하며, 기존 활성 카드의 의미는 유지한다.

DB 시작 thread가 commit하고 결과 반환이 늦을 때 취소하면 running이 남는 회귀를 실제로 재현했다. 시작 결과를 shield하여 회수한 뒤 실행을 취소 상태로 닫는다. 수정 후 원천 의미·매핑·실제 PG·공개 SDK HTTP fixture·공개 API의72건이 통과했다. 이후 같은 취소 경계의 lint 권고 표현만 suppress로 정리했다. 전체 Ruff는 통과했고0036 downgrade/upgrade를 소유 DB에서 재실행했다.

웹 타입·lint와22파일277테스트도 통과했다. API/웹 전체 회귀·실제 TCP와 브라우저·최종 두 전문 리뷰·정확 HEAD CI는 후속이다. 이 기록은 owner 병합 완료를 뜻하지 않는다.

## 2차 적대 리뷰·정리 경계와 운영 UI 보완

ed8081d를 두 전문 리뷰어가 독립 검토하여 동일 P2를 지적했다. 종결 DB 오류나 client close 오류가 정제한 원인·취소·이미 커밋된 성공을 덮을 수 있었다. 다섯 경로를 실제 실패로 고정하고 별도 safe cleanup으로 보완했다. 종결 불가는 원문 없는 고정 로그·telemetry와 기존 lease/orchestrator 회수에 맡긴다. close에는5초 상한을 둔다.

동시에 신규 dataset/공개 route가 기존 telemetry의 other/unmatched로 합쳐지는 누락도 실패 우선으로 고정했다. 고정 allowlist·cancelled 상태와 cleanup stage counter를 연결했다. 이6건은 수정 전 모두 실패했다. 수정 후 관련 이벤트·원천·DB·API·metrics102건이 통과했고, 실제 close 지연과 stage/status 상한의 별도3건도 통과했다(이 중 기존 label1건 중복).

수집 실행 UI에는 지역별 특보의 한국어 dataset명과 상세 원인·코드를 넣었다. 알 수 없는 원문·prototype 키는 그대로 노출하지 않으며 기존 색상/CSS를 바꾸지 않았다. 웹280테스트·타입/lint와 새 프로덕션 빌드도 통과했다. ed8081d의 전체 Python 회귀는 별도 실행 중이며 최종 source 차이를 구분한다. 새 정확 후보의 두 리뷰·실제 TCP/UI·최종 CI는 후속이다.

## 실제 UI·Prometheus와 종결 SQL 대기 보완

f756d91에서 실제 관리자 로그인·durable session·공개 API proxy·발령/해제 분리·페이지 변경 실패·HTTP403의 한국어 상세 원인·정상 빈 결과/close 장애·기존 WeatherValue 분리의 브라우저7흐름이 통과했다. pageerror0이며 provider 응답 대체는 소유 TCP KMA fixture뿐이다. 별도 실제 Prometheus Docker가 인증 scrape로 성공5/실패2/close1과 고정 dataset/route를 읽었다. 합계8검증이며 운영 접근은 없다. 화면 QA에서 실행 ID가 옆 필드와 겹치는 기존 문제를 확인해 색상 토큰을 유지한 채 해당 grid의 줄바꿈만 보완했다. 다음 실제 UI에서 경계 폭도 확인한다.

두 f756 리뷰의 잔여 P2는 종결 SQL 잠금 대기의 무상한이었다. 실제 PG FOR UPDATE를 유지한 provider 오류/취소2건이 수정 전 실패했다. 이제 cleanup 호출만 timeout_ms5000을 전달하고 같은 transaction의 lock_timeout/statement_timeout을 제한한다. 더 엄격한 기존 설정도 유지한다. 실제 잠금을 잡은 상태에서도 원래 오류·취소·close·계측이 먼저 반환되고 기존 session값이 유지되는3검사가 통과했다.

ed8081d에서 시작한 전체 진단 실행은1677통과/3실패로 끝났다. 실행 도중 후속 cleanup/metrics가 바뀌었으므로 정확 최종 source 전체 검증으로 주장하지 않는다. 원격 f756 CI도 같은3건이 실패했다. 두 건은 최신 metadata를0033으로 stamp한 과거 인덱스 fixture가 후속0036 테이블을 다시 만들던 구성 오류로, 의도한0034만 검증하도록 고정했다. 나머지는 새 작업을 반영해22개 기대값을 갱신했다. 실패한 경계와 인덱스 guard 전체를 포함한24검사가 통과했다. 신규 migration에서 외부의 기존 table을 임의로 허용하는 우회는 추가하지 않았다.
