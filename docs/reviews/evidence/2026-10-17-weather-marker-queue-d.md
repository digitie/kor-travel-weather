# Weather105 marker worker queue FULL 독립 적대 리뷰 D

판정: **SOURCE_PASS**, 열린 P0/P1/P2/P3 없음. D의 backend PR106 구현과 분리한 A 작성 frontend PR105 독립 리뷰다.

## 고정 대상

- PR: https://github.com/digitie/kor-travel-weather/pull/105
- HEAD: 270a4e5388cd55bd31008cfc60f30e00fe3a93c6
- base: 0acb11fc94d1427f7e920d3feed8a69fac515c21
- source2: frontend/components/weather-map.tsx 및 weather-map.test.ts
- checkpoint: weather-marker-queue-checkpoint-a-r1-20261017.json
- checkpoint SHA256: b7a77d40970f3d435150bcf0ba5deb6551cd4b446c94a39e7364a6f1a493275a
- 전체 diff SHA256: ab5260109bfa5fef33cb633a73d7449dbd1b5ab0e9e0784a9f71e4c7ce173e30

Git 고정 blob 및 checkpoint/diff SHA를 재확인했다. 두 변경 파일 전체와 관련 API signal/JSON 경로, cluster의 markers→useMemo GeoJSON/setData 경로 및 author source-only handoff를 읽었다. peer review 원문은 읽지 않았다. 원 WT 및 제품 변경0.

## 코드 검토

1. **배정·상한·종료** — weather-map.tsx:391–410의 nextChunk 선택/증가는 await 전 동기 구간이므로 같은 JS event loop에서 중복 배정되지 않는다. min(3,chunks.length) worker만 만들고 각 worker는 하나의 요청을 await한 뒤 다음 chunk를 가져간다. 60개 상한/전체 대상은 동일하며 빠른 슬롯은 느린 두 슬롯을 기다리지 않는다. 성공·실패 양쪽에서 nextChunk는 전진하므로 정상적인 finite 입력에 재시도·무한 loop가 추가되지 않는다.

2. **취소·늦은 응답·refresh** — 기존 AbortController 하나를 해당 effect의 모든 fetch에 전달하며 cleanup은 cancelled=true 후 abort한다. 성공 직후 publication 전에 cancelled를 확인하고, 실패에서도 다음 배정 전에 중단한다. lib/api.ts:301–307→154–163의 실제 signal 전달을 확인했다. 무시된 abort의 늦은 성공도 shared summariesRef를 쓰기 전에 막는다. refreshToken 변경 시 기존 cache reset 계약은 동일하며, 이전 effect의 late result가 reset 후 cache에 침투하지 않는다. mapLocations/mode/refreshToken dependency는 그대로다. summaries 갱신 자체는 effect dependency가 아니므로 새 publication 때문에 동일 queue를 재시작하지 않는다.

3. **부분 성공/실패·순서** — chunk별 성공은 즉시 cache/ref와 state에 보존한다. HTTP/network/JSON 실패 chunk는 성공 cache로 기록하거나 같은 effect 안에서 retry하지 않고 다음 chunk로 진행한다. 다음 viewport는 성공한 ID를 재사용하며 실패·누락 ID는 여전히 pending일 수 있다. old wave가 전체 settled될 때까지 성공 cache를 지연하던 동작의 의도적 개선이다. 캐시 삽입 순서와 지도 marker 출력 순서는 분리되며 mapLocations.map의 순서는 그대로 유지한다.

4. **memo·렌더** — 265–279의 markers useMemo는 mapLocations/selectedId/summaries를 정확히 의존한다. 위치·summary·선택이 바뀌면 새 모델을 만들고, detail/provider 등 무관한 state만 변하면 같은 marker 배열을 유지한다. marker id/좌표/온도/condition/특보 수/선택/aria/title/클릭 대상의 기존 필드 정의는 유지된다. 현재 cluster 수신 코드는 markers 참조로 GeoJSON을 memo하므로 이 참조 보존에 의미가 있다. in-place mutation을 새로 도입하지 않았다.

5. **CPU·메모리 한계** — queue는 최대 3개의 active client 요청만 유지하지만 기존 wave 사이 유휴 시간이 줄어 DB 부하가 더 연속될 수 있다. chunk/캐시 저장량은 대상·방문 위치 수에 비례하며 기존 전체 범위를 줄이지 않는다. 성공별 state snapshot 및 marker 재구성이 발생하므로 692개/12요청에서는 기존 wave publication 최대4 대신 최대12 state publication이 가능하다. 실제 React render 수/CPU가 정확히 3배라는 뜻은 아니다. 무관 렌더 memo 절감과 더 잦은 partial publication의 순효과는 실제 측정 전에는 알 수 없다.

6. **scope·기존 경계** — selected detail의 loading/getLatest/getForecast effect, API/BFF DTO/timeout, 요청 대상/배치60/동시3/25초 gate는 변경하지 않았다. detail loading 원인의 해결 또는 R24 해결을 이 diff로 확정하지 않는다. 잘못된 API shape·중복 location 입력의 일반 방어는 기존 계약에 의존하며 이번 diff가 이를 새로 완화하지 않는다.

## 독립 실제 source 모형

별도 D script가 고정 TS effect/memo와 실제 lib/api.ts·lib/http.ts를 메모리에서 transpile했다. global fetch만 local Promise/Response로 대체하여 외부 요청 없이 **18조건 PASS**.

- 빠른 슬롯 즉시 진행/부분 cache publication.
- 692 ID 정확히 한 번·12요청·각60 이하·최대3·finite 완료.
- 실제 base effect는 첫 완료 뒤에도 기존 wave barrier를 기다림을 별도 음성 대조.
- HTTP504/network/malformed JSON 각각 격리·retry0·다음 chunk.
- cleanup 직후 3signal abort·후속 배정/쓰기0.
- 같은 cache ref를 공유한 refresh 뒤 늦은 이전 응답 차단.
- 부분 cache 겹치는 viewport 재사용.
- 전체 cached/빈 위치/list 모드의 요청0 및 explicit refresh reset.
- fetch response와 body await 사이 cleanup 차단.
- memo의 무관 렌더 참조/선택·클릭/온도·특보/이름·좌표·viewport 반영.

모형: weather105-marker-queue-review-d-r1-20261017.model.cjs  
결과: weather105-marker-queue-review-d-r1-20261017.model.safe.json  
결과 SHA256: cd0e799d7554dc5dac9b8b63f3c7aced0f7a7dd4b4fbaf05fc3995e52b017ec7

## 전체 검사와 미검증

- 전체 source2/diff 및 관련 호출 경로 FULL read, exact checkpoint/blob 해시 재검증.
- author의 13집중/202전체/type/lint/Ruff 및 old3RED1PASS는 author evidence로 구분한다. D는 이 전체 suite를 다시 실행하지 않았고 자기 18 source 모형만 독립 실행했다.
- GH fresh 조회 exact270a: OPEN/DRAFT, python SUCCESS/frontend SUCCESS. 원문 weather105-marker-queue-review-d-r1-20261017.ci.json SHA256 21d0092e95f6a90927fdabc1b5931df0e25486b97bd9f3c97f10b774f5f11913.
- 실제 React DOM/MapLibre event lifecycle/브라우저/UI25초/서버 CPU·DB·운영 효과는 미검증. R24 35PASS1FAIL은 그대로이며 원인 해결/성능 개선의 운영 합격으로 세지 않는다.
- 외부 fetch/HTTP/SSH/Docker/DB/SDK/운영0, private auth/운영 JSON 접근0. 원본/타인 수정0. 모든 도구 terminal.

별도 backend 작업 기록: PR104 실제 headRef=codex/weather-marker-cpu-20261016/HEAD0ac 확인 후 Draft PR106을 생성·attach했다. 이후 root가 통합582로 remote branch를 fast-forward하고 PR105 위로 retarget했으며 D 로컬049와 source 검토 기준은 그대로 보존한다. 이 backend 저작분을 PR105 독립 리뷰 근거로 합산하지 않는다.
