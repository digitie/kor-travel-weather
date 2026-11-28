# ADR-103: 오래 요청되지 않은 export snapshot handle은 지운다

- 상태: 채택
- 결정일: 2026-10-27

## 맥락

export는 manifest가 바뀔 때와 TTL마다 수 MiB의 snapshot handle과 scope별 refs를 만든다.
지우는 경로가 없어서 계속 쌓였다. 한편 handle은 페이지 cursor와 "이전 성공 집합 fallback"
(W1 축소 방지 비교 포함)의 근거이기도 하다.

## 결정

만료된 지 일정 시간(6시간)이 넘은 handle은 publisher가 조금씩 지운다. 유효한 cursor(만료 전)와
계속 요청되는 selection의 최신 handle은 지우지 않는다. 첫 페이지가 handle을 재사용하면 TTL을
연장한다. 정리는 일별 drain 예산과 무관하게 tick 상한 안에서 돈다.

## 검토한 대안

- 지우지 않음: 저장량이 끝없이 늘어난다.
- 만료 즉시 삭제: 잠시 요청이 끊긴 selection도 fallback을 바로 잃는다.

## 결과

- 6시간 넘게 요청이 없던 selection은 이전 성공 집합 fallback(W1 축소 방지 비교 포함)을 잃는다.
  감수한 단점이다.
- tick당 개수, 실패 시 절반·성공 시 두 배 적응 같은 운영 값은
  [`../design/map-service-exports.md`](../design/map-service-exports.md) §3.1 "구현 정산"과 코드가 정본이다.
