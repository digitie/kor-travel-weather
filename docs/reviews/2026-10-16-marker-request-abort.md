# 마커 조회 effect 요청 취소 보강

## 문제와 변경

기존 지도 마커 effect는 cleanup에서 cancelled=true만 설정했다. 이 값은 늦은 응답의 cache/화면 반영을 막지만 getMarkerSummaries의 fetch 자체를 취소하지 않았다. viewport·조회 방식·새로고침으로 effect가 교체되면 이전 effect의 최대3요청이 새 요청과 겹쳐 남을 수 있었다. 실제 R19의 markers504/화면 미수렴과 구별되는 로컬 재현 결함이며, 해당 운영 장애의 단독 원인으로 확정하지 않는다.

마커 API helper에 optional AbortSignal을 추가하고 effect별 AbortController를 같은 effect의 모든 fetch에 전달한다. cleanup에서 기존 cancelled publication guard를 먼저 설정한 뒤 abort한다. 한 묶음60개/동시3개·부분 실패 시 성공 결과 반영·기존 cache/화면 흐름은 그대로다. 별도의 retry·timeout·backend 한도/DTO·provider·인증·선택 위치 상세 요청은 바꾸지 않는다.

## 검증

- 실제 source의 marker effect callback과 batch 상수를 TypeScript AST로 추출하고 실제 API helper까지 실행했다. fetch만 지연 대역으로 바꿨다. React DOM/MapLibre를 실제 mount한 E2E가 아니라 cleanup→새 effect 순서를 직접 재현하는 로컬 수명주기 시험이다.
- 구제품은7조건 중 취소 관련5 FAIL/기존 publication·부분 실패2 PASS였다. 수정 후 unmount·파라미터 변경·mode/refresh 변경·일부 응답 후 cleanup에서 signal.aborted와 미종료 대역 fetch0, 추가 wave 차단을 확인했다.
- 취소를 무시하고 늦게 성공하는 fetch도 기존 publication guard가 cache/화면을 갱신하지 못한다. 정상60개/3동시·일부504/성공 혼합 처리를 회귀로 고정했다.
- 전체 frontend196 PASS(기존189+신규7), type-check/lint/build PASS. 기존 experimental.cpus2도 보존했다.
- repository Ruff/diff PASS. Python 코드0변경이며 기본 Weather conftest/PG를 실행하지 않았다.

브라우저 취소는 네트워크 요청의 취소 신호이며 이미 시작된 서버 SQL의 즉시 종료·전체 요청 수 상한·운영504 해소를 보장하지 않는다. 실제 UI 배포·동일 시나리오 live API/UI·서버 쿼리 수/CPU 전후 비교는 NOT_RUN이다. 현재UI97 1c968과 후보base43ef의 frontend tree가 완전히 같음을 확인했으므로 기존 UI 통합 기능을 되돌리는 변경은 없다.
