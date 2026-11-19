# 2026-11-19 — 관리자 셸 @kor-travel/ui 0.2.3 채택과 버튼 테두리 원인

## 바뀐 것

- 자체 rail 셸(`components/admin-shell.tsx`)을 공용 `AdminShell` + `AppMenu`(UI 0.2.3, tokens 0.1.4)로 바꿨다.
  lg(1024px) 이상은 좌측 rail(접힘 4rem, 키 `kor-travel-weather:sidebar-collapsed` 유지), 미만은 상단 바 메뉴
  버튼으로 여는 좌측 drawer다. 8개 관리자 화면 모두 같은 셸을 쓴다.
- 상단 바는 한 줄이다: 제목 "날씨 - <페이지>"와 작은 설명(예: "활성 위치 카탈로그 n곳")만 둔다.
  앱 설명 워드마크("Weather Scraper admin")와 섹션 라벨·큰 설명 문단은 없앴다. 홈의 "날씨 지도"·"수집 실행"
  바로가기 버튼은 메뉴와 중복이라 지웠고, 새로고침은 모바일에서 아이콘만 보인다(라벨은 스크린리더 텍스트).
- 날씨 지도의 "지역 날씨 모두 보기 (n)" 버튼과 지역 목록(`WeatherRegionAccess`)을 지웠다. 배치된 지역 카드는
  계속 `tabindex=0` 버튼이고, 자리가 없어 숨긴 카드는 `aria-hidden`·`tabindex=-1`이다. 숨겨진 카드에 있던 포커스는
  보이는 첫 지역 카드로, 없으면 지도 캔버스로 옮긴다. 모든 위치는 "목록" 탭으로도 고를 수 있다.
- 공용 셸의 `z-kt-*` 층 변수(`--kt-z-nav` 등)를 `globals.css`에 정의했다. 이 앱은 `@kor-travel/tokens/tokens.css`를
  import하지 않아, 없으면 상단 바·skip link의 z-index가 auto가 된다.

## 버튼 테두리가 굵어지던 원인(측정)

같은 시험 코드(`새로고침` outline 버튼, 홈·API 키 화면, 1280·390)를 수정 전 main `8a4c9ac`과 수정 후 브랜치에서
GitHub Actions로 돌려 상태별 스크린샷과 computed style을 받았다(scratch 브랜치 `scratch/button-evidence-{before,after}`,
병합 대상 아님).

| 상태 | 수정 전 | 수정 후 |
|---|---|---|
| 쉼 | outline **3px solid 어두운색**(항상 보임) + border 1px | outline 없음, border 1px |
| hover | 배경이 brand 파랑으로 바뀜, 3px outline 유지 | 배경 subtle 회색 |
| Tab 포커스 | border가 focus 색으로 바뀜 + outline 2px offset 2px → **이중 링** | border 그대로 + outline 2px offset 2px 하나 |
| 누름 | 이중 링 + brand 배경 + `translate 1px` + `transform translateY(1px)` → **2px 이동** | outline 하나, `translate 1px`만(1px 이동) |

원인 세 가지:

1. 로컬 `buttonInteractionClasses`의 `outline-solid`: 폭을 주지 않아 CSS 기본값 `medium`(3px)·currentColor로
   **쉬는 상태에서도** 굵은 테두리처럼 보였다.
2. `globals.css`의 unlayered `button:focus-visible { border-color: var(--focus); outline: 2px … }`: 테두리 색까지 바꿔
   포커스 링이 테두리+outline 두 겹이 됐다. unlayered라 Tailwind utilities를 특이도와 무관하게 이긴다.
3. unlayered `button:hover { background: brand-hover }`·`button:active { transform: translateY(1px) }`: 모든 버튼
   hover가 파랑이 되고, 공용 Button의 `translate-y-px`와 겹쳐 눌림이 2px였다.

수정: 위 unlayered button/a 규칙과 `.button/.secondary/.ghost/.danger`를 지우고, 로컬 Button에서 outline/ring 덧댐을
뺐다(공용 0.2.3이 outline 2px·offset 2px·`--kt-focus`를 직접 낸다). `a { color; text-decoration }`는 `@layer base`로
옮겼다. 입력 컨트롤(input/select/textarea)의 hover·focus 규칙은 공용 0.2.3에서 바뀌지 않아 그대로 둔다. 전역
`button:disabled`가 사라져 날씨 소스 필터(`.kt-map-select-trigger`)에는 자기 범위 비활성 규칙을 따로 뒀다.

## 검증

- `e2e/admin-shell.spec.ts`(1280·390): 8개 화면 h1 하나 "날씨 - <페이지>"와 작은 설명, 앱 설명 문구 없음,
  Tab 포커스 outline 2px 하나·border/box-shadow 불변·누름 1px 이하, drawer 열기·Escape 복귀·이동 시 닫힘,
  "지역 날씨" 버튼·목록 부재와 지도 마커 키보드 선택.
- 기존 `compact-map-ui`·`map-workspace-ui` 셸 단언을 새 상단 바 계약으로 바꿨고, `weather-map.test.ts`의 목록 자동 열기
  시험을 숨긴 카드 포커스 이동 시험으로 바꿨다.
- vendor 파일 수 시험(`tests/test_normal_image_build.py`)은 UI 0.2.3의 `admin-shell.js/.d.ts`로 266→268.

## 남은 것

- 좁은 폭에서 긴 설명은 말줄임되고 전체 문장은 `title` 속성에만 있다(공용 셸 설계).
- 운영 배포는 하지 않았다(Weather는 odroid, 배포는 Codex 담당).
