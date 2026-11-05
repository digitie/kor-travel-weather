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
