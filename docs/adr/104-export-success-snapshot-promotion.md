# ADR-104: 서비스 export는 성공한 수집만 불변 snapshot으로 승격해 내보낸다

- 상태: 채택
- 결정일: 2026-10-23(설계), 2026-10-27(run 성공 뒤 승격 유지 재확인)

## 맥락

Map은 Weather 서비스 export(W1~W5, `/v1/service/exports/*`)를 cursor로 여러 페이지에 걸쳐
읽어 Valkey 세대를 교체한다(Map ADR-114). Weather의 current pointer는 mutable이고, 수집 run의
중간 chunk commit이 실패 run이어도 이미 보인다. 실패 run 이전으로 되돌리는 기능도 없다.
그래서 current pointer를 요청마다 직접 읽으면 페이지 사이에 값이 바뀌어 누락이 생기고,
실패한 run의 값이 소비자에게 나간다.

## 결정

- export는 API 호출마다 결과를 다시 만들지 않는다. 수집 run이 **terminal 성공**했고 그 run의
  expected scope가 모두 확인된 뒤에만, 직전 공개 성공 집합에 검증된 변경분을 더한 새 불변
  snapshot(manifest)을 승격한다.
- 소비자는 그 snapshot을 opaque cursor와 ETag/304로 읽는다. 한 cursor 순회는 처음부터 끝까지
  같은 snapshot을 본다.
- 승격 판정은 run 단위다. step 단위 승격은 하지 않는다. current가 늦게 공개되는 문제는 job을
  current와 forecast로 나눠 푼다(2026-10-27 소유자 결정, 2026-11-02 적용).

## 검토한 대안

- `known_at <= cutoff` 조건으로 current를 페이지마다 읽기: 페이지 사이 pointer 교체로 누락이 생긴다.
- 긴 PG exported snapshot을 HTTP 요청 사이에 유지: 연결·snapshot을 오래 쥐어 공용 DB를 막는다.
- 전체 history에서 과거 pointer를 복원: 비용이 크고 실패 run 격리를 보장하지 못한다.
- step 단위 승격: current 공개는 빨라지지만 부분 실패한 run의 값이 공개된다.

## 결과

- 소비자는 실패 run의 값을 보지 않고, 페이지 순회 중 누락·중복이 없다.
- 공개 지연은 run 길이만큼 늘어난다. 승격·artifact 저장·GC 비용과 handle 보존
  ([ADR-103](103-export-snapshot-handle-retention.md))을 운영해야 한다.
- 표준 profile 첫 페이지 사전 구성([`../export-prebuilt-pages.md`](../architecture/export-prebuilt-pages.md))은
  이 snapshot 위의 최적화이며 이 결정을 바꾸지 않는다.
- 세부 설계(receipt, scope barrier, COW artifact, cursor 형식)는
  [`../design/map-service-exports.md`](../design/map-service-exports.md) §3이 정본이다.
