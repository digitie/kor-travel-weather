# Weather 지도 작업면 높이·상세 슬롯 채택

작성일 2026-10-25(KST). [설계](../design/2026-10-25-weather-map-improvements.md)의
C-MAP-01·W-UX-01을 위한 frontend 변경이다. 첫 색상·안내 묶음
[PR #169](https://github.com/digitie/kor-travel-weather/pull/169)는 source
`415bba5edc53550baf2bc5f937a91a3dacd56c5e`에서 검증한 뒤 main
`931502e8c2724c4a471326685d6df10d972438d3`로 머지됐다. 이 문서는 그 후속 작업을 기록한다.
현재 변경의 CI·독립 리뷰·최종 머지 정본은 [Weather PR #170](https://github.com/digitie/kor-travel-weather/pull/170)이다.

## 구현 범위

- `/weather`만 `100dvh`의 유한한 shell 높이를 제공한다. 모바일 브랜드·메뉴를 한 줄로 배치하고,
  shell → main → 공용 `MapWorkspace` → `MapTabs` → `MapTabPanel` → `MapStage`로 남은 높이를 전달한다.
  기존 canvas·목록의 `70dvh`, 최소 `28rem`, 고정 높이와 중복 frame을 제거했다.
- 공용 `MapFilterPanel`은 좁은 화면과 짧은 가로 화면에서 기본 접힘으로 시작한다. 기본 label은
  `지도 필터`이고 적용된 종류·출처·검색만 요약한다. 조건과 입력 DOM은 유지한다.
- 지도 `MapStage`의 `summary`에는 조회 중·실패·빈 자료 안내를 배치한다. 목록에서는 같은 안내를
  stage 앞의 정상 문서 흐름에 배치하고 목록 본문만 남은 높이에서 스크롤한다. 카탈로그 조회 중·실패·빈 응답에도
  지도 작업면을 유지하며 재시도를 제공한다. 단순 요청 상태가 건수·좌표 줄을 늘려 지도를 밀어내지 않는다.
- 지도 건수는 범위의 여백·선택 위치를 포함해 불러온 위치 수이고 목록 건수는 검색 조건에 맞는 위치 수다.
  이 차이와 카탈로그 전체 수는 제목 `i`에서 설명한다. 전체 위치를 표시했다는 표현으로 바꾸지 않았다.
- `MapDetailOverlay`의 공용 header/body·닫기·접기를 사용한다. header에는 위치명과 출처 `i`만 두고,
  최신/오래됨·실제 수집 시각을 본문 최상단에 배치했다. 특보·실패·기온은 이어서 읽는다.
  다른 위치를 열 때 이전 위치의 접힘 상태를 넘기지 않는다. 좁은 상세에서는 측정 카드가 한 열로 배치된다.
- 목록은 공용 stage의 스크롤 하나만 사용한다. 상세 닫기/Escape 후 검색 입력이 숨겨져 있으면
  보이는 필터 버튼으로 초점을 돌린다. 지도/목록은 canvas를 유지한다.
- Weather 소유 출처 메뉴는 `open`을 제어하고 자기 trigger가 숨겨지는지 관찰해 portal을 닫는다.
  숨겨진 trigger 대신 공용 필터 버튼으로 초점을 돌린다. 외부 검색 입력을 클릭한 일반 닫기와
  resize 중 이미 보이는 외부 요소로 옮긴 초점은 유지한다.
- MapLibre의 확대·축소 아이콘 버튼은 기본 크기를 보존하며 최대44px, coarse 포인터는44px로 맞췄다.
  글자200%에서 기존 `--control-h`가72px가 되어 공용 예약 영역을 넘던 문제를 실제 재현해 수정했다.
  이 규칙은 `/weather`의 `.maplibregl-ctrl-group > button`에만 적용한다.
- 글자 200%에서 기본 화면의 지도·상세가 작아지는 실측을 바탕으로 공용 `MapWorkspace expandable`을
  채택한다. `지도 크게 보기`는 기존 작업면의 DOM·조건·지도 엔진을 유지하고 이 route의 메뉴·제목만 숨긴다.
  `원래 화면으로`와 처리되지 않은 Escape로 복귀한다. 별도 fullscreen·portal·동적 높이 hook은 만들지 않는다.

API·SDK·수집·DB·인증은 변경하지 않았다. 특보/실황/초단기/단기/중기/관측 여섯 종류,
제공처·종류를 LIMIT 전에 적용하는 요청, 현재값/미래 예보 분리, marker 세대 캐시·abort·부분 성공은 보존한다.
지도 오류는 선택 위치의 날씨 요청 오류와 분리해 지도 summary에서 확인한다.
실패한 raster source/타일은 `sourcedata`의 실제 `loaded`로 회복을 기록한다. 지도 `idle`·style/tiles 완료와
공개 `Map.coveringTiles()`의 현재 표시 범위를 함께 확인해 해당 화면에 실패 타일이 없을 때 지도 오류를 지운다.
MapLibre는 `errored` 타일도 `loaded()`/`idle`의 완료로 세므로 최초 `load`나 `idle`만으로 경고를 지우지 않는다.
같은 화면의 실패·타일 외 SDK 오류는 유지하며 일부 실패를 전체 사용 불가로 단정하지 않는 `지도 표시 오류 · 목록 보기`로 안내한다.

Common 제품 source `a74ac808752fd6d6062e529909fd0dd656724f08`의 정규 `git archive` pack을 vendor/lock으로 고정했다.
`kor-travel-ui-0.1.0-dev.8.tgz` SHA256은 `71d9f6ce8c178e3c66a47593a70a7fd9274938587ef9d96c1c7023673370059f`다.
Common 문서 head `8cb0e51cac650e757dc9fefc45fa8c42e1ab12ef`는 제품/lock 차이가 없으며,
fresh Docker의 UI76·tokens7·type/build 통과 pack과 preview가 byte 단위로 같다.

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
| 확대·축소 아이콘 겹침 | 200%에서 버튼72×72·x232와 상세 right259의27px 겹침 재현. 앱 소유 group 버튼 상한 후44×44·x260으로 비겹침 PASS |
| resize 중 외부 초점 | 이미 제목 `i`에 옮긴 초점을 출처 메뉴 닫기가 빼앗는 실패 재현. 보이는 외부 focus를 보존하는 guard 후 두 브라우저 PASS |
| 두 독립 리뷰: 타일 회복 안내 | 정상 최초 load 뒤 타일503→실패한 동일 타일200을 주입해도 경고가 남는 P2를 실제 앱·MapLibre로 양 브라우저에서 재현. 같은 타일 재시도·정상 다른 범위 pan/zoom·지속503·최초503 10개 PASS |
| 동일 키 재시도만 해제한 중간 후보 | 실패 범위를 벗어난 실제 pan 뒤 정상 타일만 보이지만 경고가 남음. Chromium 실패12·재시도0·성공51, Firefox 실패18·재시도3·성공54로 재현. 공개 coveringTiles의 현재 표시 범위를 함께 판정하도록 보완 |
| 독립 James: 목록 경고 가림 | `/locations` 성공·`/providers`503에서 첫행 y234~282의 중앙 hit-test가 경고를 가리키는 P2를 실제 앱으로 양 브라우저에서 재현. 정상 flow로 옮긴 뒤 첫행 클릭·상세/닫기·경고 접근 2개 PASS |

초기 카메라 readout 드리프트는 안정화 전 값을 비교한 영향과 분리했다. dev.7 baseline과 새 작업면의 실제 pan→목록→지도는
같은 중심/줌을 유지했다. 새 작업면에서도 0크기 guard 유무의 해당 경로가 모두 통과해, 이를 Firefox 회귀 수정의 인과 증거로 부르지 않는다.
`ResizeObserver`의 숨겨진 0×0 크기 관측은 무시하고 다시 보이는 양수 크기에서 갱신하는 최소 guard만 둔다.

증거는 `C:/Users/digit/.codex/visualizations/2026/10/25/weather-map-workspace/`에 보관한다.
`canonical-content-scroll/`, `canonical-spacing-text200/`, `expansion-preview-ca2a225/`는 문제 재현의 JSON·PNG다.
`candidate-20953b-minimum/`의 선행 UI10개 PASS와 첫 source `9a1e1a3`의 CI/Docker 결과는 이전 후보의 증거다.
source `3554f7c`의 `f15511a`/pack `866aca…`와 앱의 아이콘 상한·외부 focus guard를 함께 소비한 검증은
`final-f15511a/`의 `workspace-ui.json`, PNG, 재현 설정과 `weather-workspace-f155-validation.log`가 정본이다.
이 source의 CI `40176053743` 전체 SUCCESS·root Docker253/type·native Chromium 실제 API/타일375/1440 PASS는
해당 이전 후보의 증거이며 아래 후속 수정 source의 CI·리뷰를 대신하지 않는다.

| source `3554f7c` 로컬 검증 | 결과 |
|---|---|
| `npm run type-check` | PASS |
| `npm run lint` | 오류0·기존 `window.location.assign` 경고1 |
| `npm test -- --run` | 19파일/253개 PASS |
| `npm run build` | PASS. 컴파일2.8초·TypeScript2.1초, 15개 페이지 생성 |
| 두 브라우저 UI | Chromium/Firefox 전체32개 PASS(2.2분) |

전체32개는 기존 공용 종류/출처4개·메타 팝업/테마4개와 작업면24개다. 375/320/1440·가로812·414/768·글자200%,
catalog 로딩/빈 응답/503, 단일 목록 스크롤, 실제 pan 후 지도/목록 왕복·회전/resize, 필터 조건 보존을 포함한다.
양방향 반응형 전환 뒤 초점이 보이는 필터 안에 남고, portal 기준점이 숨겨지면 닫히며 이미 보이는 외부 focus는 보존한다.
200%에서 `i` Escape는 상세와 확장을 유지하고 상세 Escape는 상세만 닫는다. 다음 Escape와 복귀 버튼은 원래 화면·버튼 초점을
복원하며 canvas DOM·카메라를 유지한다. 필수 상태·특보·기온은 본문 스크롤 후 실제 viewport90%로 읽을 수 있다.

| 최종 환경(두 브라우저 동일) | stage y/높이/아래 여백 |
|---|---|
| 375×812 | 233 / 575 / 4px |
| 320×568 | 233 / 331 / 4px |
| 1440×900 | 152 / 724 / 24px |
| 812×375 | 203 / 168 / 4px |
| 414×896 | 233 / 659 / 4px |
| 768×896 | 287 / 605 / 4px |
| 320×568 글자200% | 기본 로딩/성공436 / 124 / 8px → 명시 확장279 / 285 / 4px |

200% 확장의 상세 body122px·닫기44×44, 상세 right259px·zoom left260px로 조작 영역이 겹치지 않는다.
위 source 검증 뒤 전용24135 개발 서버를 중지했다. 후속 P2 재현에는 같은 소스 복사본을 다시 사용했다.
타일 주입 검증은 운영 키 없이 `layout-tile-fixture-only` 공개 fixture 키를 설정하고 정확한 키 경로의
모든 요청에 자체 생성256px PNG 또는503을 응답한다. `e2e/map-tile-recovery.spec.ts`는 실제 MapLibre와
실제 UI 줌으로 정상 최초 load·실패·동일 타일 재시도를 검증하며 운영 VWorld 서비스 수용과 구분한다.
`tiles-before-recovery-fix/`와 `list-provider-error-before/`는 수정 전 실패,
`final-a74ac80-tiles/`는 동일 키 재시도만 확인한 중간6개이고, `tiles-pan-before/`는 그 후보에서
다른 정상 범위로 pan한 뒤에도 경고가 남는 실패 증거다. 최종10개는 `final-a74ac80-current-view-tiles/`를 따른다.
`list-provider-error-fixed/`는 목록 가림 수정 뒤 두 브라우저 증거다.

## P2 수정 후 최종 로컬 검증

Common `a74ac80`/pack `71d9f6…`와 타일 회복·목록 경고 수정을 함께 검증했다.
`final-a74ac80-current-view-workspace/`의 `workspace-ui.json`·PNG·실행 설정,
`weather-map-a74-current-view-validation.log`·`weather-map-a74-current-view-ui.log`가 최종 로컬 작업면 증거다.

| 검증 | 결과 |
|---|---|
| WSL `npm run type-check` | PASS |
| WSL `npm run lint` | 오류0·기존 `window.location.assign` 경고1 |
| WSL `npm test -- --run` | 19파일/253개 PASS |
| WSL `npm run build` | PASS. 15개 페이지 생성 |
| Chromium/Firefox 반응형 UI | 기존32개+출처503 목록 회귀2개=34개 PASS(2.6분) |
| Chromium/Firefox 타일 상태전이 | 정상 load 뒤503→같은 타일200·정상 다른 범위 pan/zoom·지속503·최초503, 10개 PASS(1.3분) |
| `git diff --check` | PASS |

두 브라우저의 375/320/1440·가로812·414/768 및 글자200% 치수는 위 표와 동일하다.
필터/조건·카메라·확장/복귀·중첩 Escape·출처 portal·닫기44px·특보/시각/기온의 본문 스크롤 접근을 보존한다.
최종 전용24135 서버는 PID·명령·CWD가 `/tmp/weather-map-workspace-dev8` 소유임을 확인한 뒤 중지했다.
소스 복사본과 스크린샷·보고서는 보존했다.

## 남은 수용과 경계

- 최종 source의 Docker 2차·CI·독립 두 리뷰와 공용 Common/Map/Transport 통합 게이트는 PR #170에서 별도 정산한다.
- 반응형 UI는 키 없는 단색 배경으로, 타일 상태전이는 전용 키·PNG/503 주입으로 검증한다.
  source `3554f7c`의 별도 실제 API/타일 검증과 구별하며 최종 source 운영 제공처·실기기 Android/iOS 수용으로 확대하지 않는다.
- W-UX-02의 시간별 우선 구성·관심 요소·시각 선택, W-UX-03의 주소/교통/실행 연결과 API 보완은 이번 범위가 아니다.
- CI·독립 두 리뷰·Docker 2차·머지·배포는 별도 게이트다. 이 로컬 후보 기록으로 완료를 추정하지 않는다.
