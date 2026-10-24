# 공용 지도 작업면 채택 후보 — 2026-10-24

## 변경과 고정 산출물

Map Feature 작업면을 공용화한 Common `e79816afb3c19b1b1e2fc1486b809c2ee3d34d6e`를 소비한다.
[공용 API·치수 계약](https://github.com/digitie/kor-travel-common/pull/45).
특보·실황·초단기·단기·중기·관측 종류 필터를 제공한다. 제공처와 종류는 같은 저장 행에서 LIMIT 전에 선택한다. 특보는 observed와 분리한다. 마커는 metric별 대표를 보존하고 미래 예보는 별도 forecast 배열로 반환한다. OpenAPI와 TypeScript client를 함께 갱신했다. KMA 중기 문자열·강수 우선순위를 보강하고 상세는 지도 위에서 닫기/Escape·포커스 반환을 지원한다.

`kor-travel-ui-0.1.0-dev.7.tgz` SHA256은 `4445c694013bd19fb57d3655cfc25d86e99a6733d655524718be3bf769ba7422`다.
Map/Transport/Weather가 같은 산출물과 lock integrity를 고정한다.

## 검증과 통합 경계

WSL 타입·frontend251·종류/제공처 PostgreSQL20 PASS. Chromium375/1440px 필터30px·176px·PC한줄·실제좌표/줌·종류요청·상세배치·선택위치이동·Escape/포커스·가로 overflow2 PASS. 독립 Popper 지적을 반영했다. 전체 Python·CI·최종 소비자 통합 gate는 아직 정산 전이다.

최신 main106a31e의 Next16.4.3·React19.3.1·MapLibre6.15 보안 정합을 통합하고 Base UI1.9를 고정했다.
현재값과 미래 강수를 섞어 표시하던 Popper P2를 재현해 현재값 우선·예보는 같은 가장 이른 대상 시각으로
상태와 온도를 선택하도록 수정했다. 미래 시각의 온도를 빌려오지 않는 회귀를 포함해 WSL 타입/lint·253개·빌드와
375/1440px Chromium2개(12.2초)가 통과했다. 배경은 실제 MapLibre이며 API 응답은 결정적 fixture로 UI를 검증했다.
원본의 사용자 untracked DB4개는 보존했다. 전체 CI와 정확 후보 독립 재확인은 별도 gate다.

첫 최종 Python CI는1280개 통과·2개 실패였다. 실패는 고정2026-10-23 중기 발표에 현재 조회 시각을 붙여
18시간 유효기간을 넘긴 기존 native head fixture였다. 실제 lifecycle 시각에 맞는 최근06/18시 fixture를 사용하도록
테스트만 갱신했다. 운영의18시간 제한은 유지하며 격리 PostgreSQL에서 관련89개와 Ruff를 통과했다.
최신 전체 Python CI를 다시 확인한다. 프론트엔드 실행 소스는 e5a6d1b와 같다.

ee82c26의 CI40060554023에서 Python/프론트엔드 전체가 통과했다. James 최종 리뷰의 추가P2 두 건은
마커에서 전체 선택도 명시적인6종을 요청하고, 목록 empty를 catalog 대기/실패 중에는 표시하지 않도록 고쳤다.
무필터 API의 기존 현재값 전용 계약과 초기 위치 카탈로그 조회 동작은 유지한다. WSL 타입/lint·253개·빌드,
375/1440px UI2개(12.9초)가 통과했다. UI 검증은 기본6종 요청·단일 중기·의도적인 조회 지연/502·정상 빈 결과를 포함한다.

사용자 지시로 운영 빌드·배포는 다른 통합 에이전트가 담당하며 이 후보는 배포하지 않았다.
Weather 종류 필터는 API/UI를 함께 반영해야 한다. UI만 먼저 배포하면 서버 필터가 적용되지 않는다.
