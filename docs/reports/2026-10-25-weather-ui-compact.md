# Weather 작은 UI 묶음 구현·로컬 검증

작성일 2026-10-25(KST). [설계](../design/2026-10-25-weather-map-improvements.md)의
W-UX-01·02 중 색상·안내·헤더 밀도만 부분 구현했다. 최종 CI·독립 리뷰·머지 상태는
[Weather PR #169](https://github.com/digitie/kor-travel-weather/pull/169)의 Checks와 리뷰 기록이 정본이다.

## 변경 범위

- Common `@kor-travel/ui`는 기존 `0.1.0-dev.7`을 유지했다. Weather의 navy 표면·문자·브랜드·초점·상태·그림자·폰트를
  공용 `--kt-*`에 의미별 연결했다. `.dark`에서도 같은 별칭을 다시 평가한다. 특보의 의미색은 브랜드와 분리했다.
- 날씨 지도 제목의 반복 설명은 제목 바로 옆 `i`로 옮겼다. `/weather`만 compact 헤더를 사용해 둥근 내부 카드와
  이중 여백·경로 표시를 걷어냈다. 다른 페이지의 기본 헤더는 유지한다.
- 상세의 위치 ID·좌표·조회 시각·제공처·데이터셋·원본 키는 제목 옆 `i`에서 확인한다. 원본 키는 잘라내지 않으며,
  긴 목록은 팝업 내부에서 스크롤한다. 기존 측정값 행의 데이터셋 표시는 유지한다.
- 위치 이름·최신/오래됨·수집 시각·특보·값·실패는 본문에 남긴다. 수집 시각이 없을 때 조회 완료 시각을 대신 보여주던
  표현은 `수집 시각 없음`으로 바꿨다. 관측·예보 시각을 해석하는 추가 기능은 이번 범위가 아니다.
- `i`는 Transport의 기존 Base UI Popover 패턴을 재사용했다. 키보드·터치로 열고 닫으며 coarse 포인터의 hit area는
  44×44px, 아이콘은 15px다. 일반 포인터는 기존 compact 크기 28×28px를 쓴다.

백엔드·API·수집·DB·Common 패키지·운영 빌드·배포는 변경하지 않았다.

## 로컬 검증

WSL2 `Ubuntu-26.04`, Node `v22.22.2`에서 작업했다. Windows 원본 frontend를 다음처럼 복사한
`/tmp/weather-ui-compact-batch`가 테스트 소스다. 전용 dev 서버는 `127.0.0.1:24125`를 사용했다.

```bash
rsync -a --exclude=node_modules --exclude=.next --exclude=test-results --exclude=playwright-report \
  /mnt/f/dev/kor-travel-weather-decision-design/packages/kor-travel-weather-admin/frontend/ \
  /tmp/weather-ui-compact-batch/
```

`npm ci` 후 `node scripts/copy-maplibre-worker.mjs`를 실행했다. 로컬 전용 인증 설정으로 `next dev`를 띄웠으며
운영 키나 실제 제공처 응답을 사용하지 않았다. 실제 앱과 로그인 세션을 통과한 뒤 날씨 조회만 Playwright fixture로 대체했다.

| 항목 | 결과 |
|---|---|
| `npm run type-check` | PASS |
| `npm run lint` | 오류 0. 기존 `admin-shell.tsx`의 `window.location.assign` 경고 1개 유지 |
| `npm test` | 19파일·253개 PASS |
| 기존 `e2e/shared-map-ui.spec.ts` | Chromium·Firefox × 375/1440px, 4개 PASS |
| 신규 `e2e/compact-map-ui.spec.ts` | Chromium·Firefox × 375×812/1440×900, 4개 PASS |

총 UI 8개 PASS(최종 실행 41.4초). 임시 Playwright 설정은 기존 두 파일을 `workers:1`로 실행하고 Chromium/Firefox
프로젝트를 지정했다. Chromium은 SwiftShader, Firefox는 `webgl.force-enabled`·소프트웨어 렌더링을 사용했다.
375px 신규 시나리오는 `hasTouch:true`로 실제 `pointer:coarse`와 44px hit area를 확인했다.

신규 검증은 다음 행동과 결과를 포함한다.

1. 제목·상세의 `i`를 키보드로 열고 닫는다. 팝업 Escape는 상세를 유지하고 `i`로 초점이 돌아온다.
   상세 Escape는 검색 입력으로 초점을 돌린다.
2. 위치 ID·좌표·긴 원본 키는 팝업에서만 보이고 32개 원본의 목록은 팝업 내부에서 스크롤한다.
   팝업이 화면 좌우를 넘지 않고 스크롤 중 배경 페이지 위치가 변하지 않는다.
3. 라이트·다크에서 공용 surface/text/brand/focus/warning/destructive 색상이 Weather 토큰과 같고,
   팝업 표면도 해당 프로젝트 표면을 따른다.
4. 오래된 수집값·호우주의보·기온은 바로 보인다. 최신값 정상 빈 응답은 `수집 시각 없음`, 503은 실패 문구를 본문에 남긴다.
5. 기존 종류 요청·필터 30px/176px·PC 한 줄·좌표/줌·목록/지도·상세 닫기·catalog 지연/실패/정상 empty 회귀를 유지한다.

스크린샷과 JSON은 작업 산출물 폴더
`C:/Users/digit/.codex/visualizations/2026/10/25/weather-ui-compact/`에 보관했다.
예: `weather-compact-chromium-375-light.png`, `weather-compact-chromium-375-light-info.png`,
`weather-compact-firefox-1440-dark.png`, `compact-ui.json`.

## 남은 수용과 경계

- 지도는 키 없는 단색 배경으로 UI만 검증했다. **운영 VWorld 타일·실제 기상 자료 성공의 증거가 아니다.**
- compact 헤더로 여백은 줄었지만 모바일 지도 시작은 Chromium 약427px·Firefox 약441px다.
  설계의 240px 이내 목표와 실제 남은 높이 활용은 아직 충족하지 않는다. 기존 70dvh/최소 높이를 바꾸거나
  별도 동적 높이 hook을 만들지 않았다. C-MAP-01의 공용 작업면·모바일 상세/제어 가림·Map 회귀가 다음 묶음이다.
- iOS Safari·Android 실기기·320px/가로·글자200% 및 실제 운영 릴리스는 미검증이다.
- W-UX-02의 시간별 우선 구성·관심 요소·시각 선택과 W-UX-03의 API 보완은 미완이다.
- Docker 2차 검증과 CI·독립 리뷰는 이 문서에서 통과로 추정하지 않는다. PR #169의 정확한 검증·리뷰 결과로 판단한다.
