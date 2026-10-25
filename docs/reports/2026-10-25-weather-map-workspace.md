# Weather 지도 작업면 높이·상세 슬롯 채택

작성일 2026-10-25(KST). [설계](../design/2026-10-25-weather-map-improvements.md)의
C-MAP-01·W-UX-01을 위한 frontend 변경이다. 첫 색상·안내 묶음
[PR #169](https://github.com/digitie/kor-travel-weather/pull/169)는 source
`415bba5edc53550baf2bc5f937a91a3dacd56c5e`에서 검증한 뒤 main
`931502e8c2724c4a471326685d6df10d972438d3`로 머지됐다. 이 문서는 그 후속 작업을 기록한다.

## 구현 범위

- `/weather`만 `100dvh`의 유한한 shell 높이를 제공한다. 모바일 브랜드·메뉴를 한 줄로 배치하고,
  shell → main → 공용 `MapWorkspace` → `MapTabs` → `MapTabPanel` → `MapStage`로 남은 높이를 전달한다.
  기존 canvas·목록의 `70dvh`, 최소 `28rem`, 고정 높이와 중복 frame을 제거했다.
- 공용 `MapFilterPanel`은 좁은 화면과 짧은 가로 화면에서 기본 접힘으로 시작한다. 기본 label은
  `지도 필터`이고 적용된 종류·출처·검색만 요약한다. 조건과 입력 DOM은 유지한다.
- `MapStage`의 `summary`에는 조회 중·실패·빈 자료 안내를 배치한다. 카탈로그 조회 중·실패·빈 응답에도
  지도 작업면을 유지하며 재시도를 제공한다. 단순 요청 상태가 건수·좌표 줄을 늘려 지도를 밀어내지 않는다.
- 지도 건수는 범위의 여백·선택 위치를 포함해 불러온 위치 수이고 목록 건수는 검색 조건에 맞는 위치 수다.
  이 차이와 카탈로그 전체 수는 제목 `i`에서 설명한다. 전체 위치를 표시했다는 표현으로 바꾸지 않았다.
- `MapDetailOverlay`의 공용 header/body·닫기·접기를 사용한다. header에는 위치명과 출처 `i`만 두고,
  최신/오래됨·실제 수집 시각을 본문 최상단에 배치했다. 특보·실패·기온은 이어서 읽는다.
  다른 위치를 열 때 이전 위치의 접힘 상태를 넘기지 않는다. 좁은 상세에서는 측정 카드가 한 열로 배치된다.
- 목록은 공용 stage의 스크롤 하나만 사용한다. 상세 닫기/Escape 후 검색 입력이 숨겨져 있으면
  보이는 필터 버튼으로 초점을 돌린다. 지도/목록은 canvas를 유지한다.
- Weather 소유 출처 메뉴는 `open`을 제어하고 자기 trigger가 숨겨지는지 관찰해 portal을 닫는다.
  숨겨진 trigger 대신 공용 필터 버튼으로 초점을 돌린다. 외부 검색 입력을 클릭한 일반 닫기는 해당 입력의 초점을 유지한다.
- 글자 200%에서 기본 화면의 지도·상세가 작아지는 실측을 바탕으로 공용 `MapWorkspace expandable`을
  채택한다. `지도 크게 보기`는 기존 작업면의 DOM·조건·지도 엔진을 유지하고 이 route의 메뉴·제목만 숨긴다.
  `원래 화면으로`와 처리되지 않은 Escape로 복귀한다. 별도 fullscreen·portal·동적 높이 hook은 만들지 않는다.

API·SDK·수집·DB·인증은 변경하지 않았다. 특보/실황/초단기/단기/중기/관측 여섯 종류,
제공처·종류를 LIMIT 전에 적용하는 요청, 현재값/미래 예보 분리, marker 세대 캐시·abort·부분 성공은 보존한다.
지도 오류는 선택 위치의 날씨 요청 오류와 분리해 지도 summary에서 확인한다.

Common source `20953b7707f6dd46324bf6b15548a26a5d9b8880`의 정규 `git archive` pack을 vendor/lock으로 고정했다.
`kor-travel-ui-0.1.0-dev.8.tgz` SHA256은 `65480d85e1ebfc6a7fd4c65ecc66e539c6f57e90fe0743c3877827266f117169`다.

## 검증 방법

Windows 원본 frontend를 WSL2 `Ubuntu-26.04`의 `/tmp/weather-map-workspace-dev8`에 복사했다.
Node `v22.22.2`와 로컬 전용 인증 설정의 `next dev`(`127.0.0.1:24135`)를 사용했다.

```bash
rsync -a --exclude=node_modules --exclude=.next --exclude=test-results --exclude=playwright-report \
  /mnt/f/dev/kor-travel-weather-decision-design/packages/kor-travel-weather-admin/frontend/ \
  /tmp/weather-map-workspace-dev8/
```

`npm ci`와 `node scripts/copy-maplibre-worker.mjs` 뒤 `npm run type-check`, `npm run lint`,
`npm test -- --run`을 실행한다. UI는 실제 로컬 로그인/앱과 MapLibre를 사용하고 날씨 API 응답만 fixture로 제공한다.
Chromium은 SwiftShader, Firefox는 `webgl.force-enabled`와 소프트웨어 렌더링을 사용한다.
375/320/414/768/가로 시나리오는 `hasTouch:true`로 coarse hit area도 확인한다.

`e2e/shared-map-ui.spec.ts`, `e2e/compact-map-ui.spec.ts`, `e2e/map-workspace-ui.spec.ts`를
전용 Playwright 설정의 두 브라우저로 실행한다. 초기 geometry는 입력·마커 초점으로 인한 자동 스크롤 전에 기록한다.
320px 글자200%는 catalog 로딩과 성공 안정 상태를 모두 측정한다. 상세의 필수 텍스트는 실제 본문 스크롤 뒤
viewport에서 읽을 수 있는지도 확인한다. `.toBeVisible()`만으로 clipping을 통과시키지 않는다.

## 후보 실측·문제 재현과 해결

아래는 초기 후보의 실패와 현재 후보의 해결을 구분한 실측이다. 전체 최종 매트릭스는 별도로 정산한다.

| 후보/환경 | 실측·결과 |
|---|---|
| 첫 UI PR #169, 375px | 지도 시작 약427~441px. 전체 높이 수용 미완 |
| 공용 v2 + 작은 route 여백, 375×812 | 두 브라우저 stage y233/h575, 아래 여백4px |
| 같은 후보, 320×568 | 두 브라우저 y233/h331, 아래 여백4px |
| 같은 후보, 1440×900 | 두 브라우저 y152/h724, 아래 여백24px |
| 같은 후보, 812×375 | 두 브라우저 y203/h168. 기본 필터 접힘과 상세 필수값 스크롤 접근 PASS |
| v2의 글자200% | 기본 y461/h99, 상세 body0·닫기 clipping 재현. 실패 |
| 확장 preview `ca2a225`, 글자200% | 기본 y436/h124 → 확장 y279/h285. canvas 유지·닫기44px 노출 PASS. 긴 위치명이 header225px를 차지해 body0으로 실패 |
| Chromium 1440→375 필터 축소 | 검색 focus가 숨은 입력에서 body로 떨어지는 실패 재현. 공용 후속 보강 대상으로 전달 |
| `20953b` 글자200% | 기본 로딩/성공 y436/h124 동일 → 확장 y279/h285. 상세 header103/body122/닫기44×44. 본문 스크롤로 최신·특보·20°C 읽기 PASS |
| `20953b` + 앱 출처 메뉴 복구 | 두 브라우저 입력 resize focus·portal 닫기/필터 버튼 복귀·일반 outside 검색 초점 유지 PASS |

초기 카메라 readout 드리프트는 안정화 전 값을 비교한 영향과 분리했다. dev.7 baseline과 새 작업면의 실제 pan→목록→지도는
같은 중심/줌을 유지했다. 새 작업면에서도 0크기 guard 유무의 해당 경로가 모두 통과해, 이를 Firefox 회귀 수정의 인과 증거로 부르지 않는다.
숨겨진 0×0 canvas에는 `resize()`를 보내지 않고 다시 보이는 양수 크기에서 갱신하는 최소 guard만 둔다.

증거는 `C:/Users/digit/.codex/visualizations/2026/10/25/weather-map-workspace/`에 보관한다.
`canonical-content-scroll/`, `canonical-spacing-text200/`, `expansion-preview-ca2a225/`는 문제 재현의 JSON·PNG다.
현재 `candidate-20953b-minimum/`의 UI10개는 두 브라우저의 375/1440·200%·반응형 입력·출처 메뉴 시나리오를
42.8초에 통과한 증거다. 같은 `65480d…` pin에서 타입·lint(오류0/기존 경고1)·Vitest19파일/253개도 PASS했다.
200%에서 `i` Escape는 상세와 확장을 유지하고 상세 Escape는 상세만 닫는다. 다음 Escape와 복귀 버튼은 원래 화면·버튼 초점을
복원하며 canvas DOM·카메라를 유지한다. 전체 UI·package build·Docker·CI·리뷰는 정산 중이다.

## 남은 수용과 경계

- 남은 전체 UI는 320/가로/414/768·초기 로딩/빈 응답/실패와 기존 두 파일의 종류/출처·긴 원본 키·라이트/다크 회귀를 포함한다.
- package build·Docker 2차·CI·독립 두 리뷰와 공용 Common/Map/Transport 통합 게이트는 아직 이 로컬 결과에 포함하지 않는다.
- 키 없는 단색 배경으로 UI를 검증했다. 운영 VWorld 타일·실제 기상 제공처 성공이나 실기기 Android/iOS 수용의 증거가 아니다.
- W-UX-02의 시간별 우선 구성·관심 요소·시각 선택, W-UX-03의 주소/교통/실행 연결과 API 보완은 이번 범위가 아니다.
- CI·독립 두 리뷰·Docker 2차·머지·배포는 별도 게이트다. 이 로컬 후보 기록으로 완료를 추정하지 않는다.
