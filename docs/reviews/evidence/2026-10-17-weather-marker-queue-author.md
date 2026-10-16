# Weather marker worker queue 인계

- 초안 PR105: https://github.com/digitie/kor-travel-weather/pull/105
- HEAD: 270a4e5388cd55bd31008cfc60f30e00fe3a93c6
- 기준: 0acb11fc94d1427f7e920d3feed8a69fac515c21 (PR104 branch)
- 별도 ext4 WT: /home/digitie/dev/kor-travel-weather-codex-marker-queue-a-20261017
- 소유 파일은 components/weather-map.tsx 및 weather-map.test.ts 두 개다. 다른 제품/문서/WT 변경 0, git clean, push 완료, merge/deploy 0.

## 결과

marker 요청을 60개/동시최대3의 worker queue로 처리한다. worker 하나가 끝나면 다른 느린 요청을 기다리지 않고 다음 chunk를 시작한다. 성공한 chunk를 즉시 cache/publication하고 실패한 chunk는 retry 없이 격리한다. viewport 전체 대상 ID를 그대로 유지한다. 기존 abort/supersession guard, cleanup 뒤 늦은 cache/화면 쓰기 금지, refresh cache reset을 보존한다.

marker 모델은 mapLocations/selectedId/summaries를 정확한 useMemo dependency로 사용한다. 상세/provider 등 다른 상태만 바뀌면 같은 markers 참조를 유지해 VWorldWeatherClusters의 불필요한 GeoJSON 재구성을 피한다. 선택/summary/viewport·이름·좌표 변경과 클릭 대상은 반영한다.

선택 상세 loading/latest/forecast API/BFF/백엔드 SQL은 변경하지 않았다. 처음 선택1개→viewport692개 전환의 취소1개는 유지될 수 있다. batch크기/동시성/대상 범위/25초 gate/HTTP 실패 기준은 완화하지 않았다.

## 실제 검증과 실패 보존

| 검증 | 결과 | 범위 |
| --- | --- | --- |
| 실제 TS effect 제어 Promise 집중 | 13 PASS | 먼저 끝난 slot 진행, 692unique/12requests/최대3, partial failure/no retry, abort/늦은 응답 차단, partial cache 재사용, marker 참조/deps |
| frontend 전체 | 202 PASS / 15files | npm test, 로컬 fixture 네트워크 mock |
| type-check/lint/Ruff | 모두 EXIT0 | 로컬 |
| 원0ac immutable copy에 새4조건 | 3 FAIL / 1 PASS / 9 deselected | 느린 요청 barrier와 없는 useMemo에서 실제 RED, 원문 로그 보존 |
| 초기 수정본 집중 실행 | 12 PASS / 1 FAIL | 기존 wave당 publication1 기대값이 남은 테스트였다. 응답별 publication3으로 맞춘 뒤13 PASS. 최초 도구 출력은 세션 원문에 보존되며 baseline RED와 구별한다. |
| Python 전체 로컬 | 미실행 | frontend만 변경, DB 접근 금지. root 통합 전체 회귀 또는 GitHub Python CI 결과는 별도로 집계한다. |
| GitHub exactHEAD CI | 확인 당시 python/frontend IN_PROGRESS | 성공으로 선기재하지 않음 |
| 브라우저/운영 성능/R24 통과 | 미실행·미입증 | root 독립2리뷰/통합/같은 기준 실제 관측 필요 |

전체202/type/lint는 최종 커밋 전 marker memo 본문 들여쓰기만 정리하기 직전에 완료했다. 최종 커밋은 해당 공백 외 의미 변경이 없고 Ruff/diff-check 및 git clean을 후행 확인했다.

## 한계와 다음 gate

R24는 35 PASS / 1 FAIL을 그대로 유지한다. 안전투영에서 13trace(초기 abort1+정상12), unique692, 마지막 body24.660초, abort24.976초를 확인했으나 최종 selectedLoading/mapLoaded 상태는 없었다. 이번 변경이 원 실패를 해결했다고 주장하지 않는다. 각 성공마다 publication하므로 기존 wave당4회보다 최대12회 source 갱신이 발생할 수 있으며 실제 CPU·wall 재측정이 필요하다. queue는 동시상한만 유지하므로 이전 wave 사이 유휴구간보다 DB 작업이 연속될 수 있다.

다음은 root가 조율하는 독립2인 제품 검토→D backend 변경과 충돌 없는 통합→필수 통합 시험/CI→실제 배포 및 동일25초 UI 조건 관측이다. 본인 제품 자기검증을 독립리뷰 수로 세지 않는다.

Checkpoint SHA256: b7a77d40970f3d435150bcf0ba5deb6551cd4b446c94a39e7364a6f1a493275a
