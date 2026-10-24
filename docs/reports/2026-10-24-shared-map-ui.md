# 공용 지도 작업면 채택 후보 — 2026-10-24

## 변경과 고정 산출물

Map Feature 작업면을 공용화한 Common `e79816afb3c19b1b1e2fc1486b809c2ee3d34d6e`를 소비한다.
[공용 API·치수 계약](https://github.com/digitie/kor-travel-common/pull/45).
특보·실황·초단기·단기·중기·관측 종류 필터를 제공한다. 제공처와 종류는 같은 저장 행에서 LIMIT 전에 선택한다. 특보는 observed와 분리한다. 마커는 metric별 대표를 보존하고 미래 예보는 별도 forecast 배열로 반환한다. OpenAPI와 TypeScript client를 함께 갱신했다. KMA 중기 문자열·강수 우선순위를 보강하고 상세는 지도 위에서 닫기/Escape·포커스 반환을 지원한다.

`kor-travel-ui-0.1.0-dev.7.tgz` SHA256은 `4445c694013bd19fb57d3655cfc25d86e99a6733d655524718be3bf769ba7422`다.
Map/Transport/Weather가 같은 산출물과 lock integrity를 고정한다.

## 검증과 통합 경계

WSL 타입·frontend251·종류/제공처 PostgreSQL20 PASS. Chromium375/1440px 필터30px·176px·PC한줄·실제좌표/줌·종류요청·상세배치·선택위치이동·Escape/포커스·가로 overflow2 PASS. 독립 Popper 지적을 반영했다. 전체 Python·CI·최종 소비자 통합 gate는 아직 정산 전이다.

사용자 지시로 운영 빌드·배포는 다른 통합 에이전트가 담당하며 이 후보는 배포하지 않았다.
Weather 종류 필터는 API/UI를 함께 반영해야 한다. UI만 먼저 배포하면 서버 필터가 적용되지 않는다.
