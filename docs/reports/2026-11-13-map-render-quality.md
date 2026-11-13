# 지도 대표값 비용과 전체 품질 검사 후속

## 개선

같은 시도/시군구 단계 안의 작은 줌 변화는 대표 위치를 다시 구성하지 않는다.
대표 위치의 거리·ID 동률 규칙은 유지하면서 그룹 전체 정렬 대신 선형 최솟값을 고른다.
5만 합성 지점/16그룹에서 Node22.23.1로9회 교대 실행한 중앙값은 아래와 같다.

| 항목 | 이전 | 이후 |
| --- | ---: | ---: |
| 실행 시간 | 21.24ms | 6.79ms |
| CPU 시간 | 22.14ms | 6.68ms |

시도/시군구/상세 세 단계의 직렬화 결과가 같고 시도 결과 SHA256은
`7adc6d2ca28fa7325e334b3ca0db9be422f9af85fdc12147c197dba9401280f7`이다.
이는 브라우저 그룹 계산의 국소 합성 측정이며 HTTP·운영 p95·전국 읽기 속도 개선율이 아니다.
새 카탈로그가 같은 위치 ID의 좌표를 바꾸면 이전 좌표의 행정 도형을 새 날씨와 섞지 않는다.

## 전체 검사에서 놓친 항목의 보완

#231은 집중 시험 뒤 머지됐으나 전체 CI frontend의 Doctor가 실패했다.
원문 `weather231-ci-front-failure.log`를 보존하고 이 후속에서 처리했다.

- 상태 updater 안의 선택 callback을 밖으로 옮겨 엄격 모드 재실행의 중복 알림을 막는다.
  이벤트 상태를 즉시 갱신해 같은 batch의 두 클릭도 보존하고 최신 callback을 사용한다.
- 전체 Motion 대신 LazyMotion/m을 사용하고 로딩 프레임 함수는 EffectEvent로 연결한다.
  Transport의 검증된 `f4cc5aad` 세 공유 helper를 소비하되 Weather의 기존 Children.forEach
  ESLint 지시문은 유지했다. 상류 SHA·설치 SHA·변형은 vendor 원장에 남긴다.
- 공식 compound renderer 구조와 utility export는 정확한 선언/규칙에만 사유 있는 Doctor
  주석 예외를 둔다. 이는 그 구조의 전면 재설계가 아니며 디렉터리·전역 규칙·게이트를 낮추지 않는다.
- 클릭 범례는 native button과 선택 상태를 제공한다. 캐시 후보는 명시적으로 존재를 확인하고
  퇴출하며, 선택 보존식과 지역 접근 UI를 응집된 단위로 나눠 복잡도를 줄였다.
- BFF의 읽기용 Geo POST·상한 메모리 캐시와 JSON await 뒤의 실제 abort 검사를 오탐으로 분류했다.
  정확한 선언/효과에만 근거를 주석으로 남겼고 실제16MiB·취소/늦은 응답 회귀를 유지했다.

## 검증 경계

최종 제품7abf88d는 WSL 전체349PASS와 같은 Docker 전체349PASS, 타입 검사0,
전체 ESLint 경고0, 공통 산출물 일치, Doctor0.9.22 전체105파일 진단0이다.
겹치는 시험과 중간348PASS를 합산하지 않는다. 새 시험 대역 이름의 lint 경고1개와
처음의 Doctor 실패는 보존하고 규칙을 완화하지 않았다.

범례 native 동작·같은 batch 선택·최신 callback·지도 선택/필터/경계·메모이제이션을
실제 소스 기반으로 검산했다. Transport의 같은 helper 엄격 모드/실브라우저 수용은
공유 소스의 증거이며 Weather 앱 전체의 실화면 수용으로 소급하지 않는다.

원문은 Git 밖 `C:/Users/digit/.codex/tmp/weather-transport-evilcharts-20261112`의
`weather-render-benchmark-20261113`, `weather-final-quality-20261113`,
`weather-final-docker-20261113`, `weather-shared-chart-fix-20261113.json`이다.
이미지 빌드·운영 DB/환경/일정·키·배포 변경은 없다. 최종 PR의 실제 CI는 별도로 정산한다.

## 작은 화면 CI 회귀와 실제 상세 fixture

#232의 최초 CI는 Doctor·lint·타입·단위349개·Next 빌드를 통과했지만 실제 Playwright
20개 중320×568/글자200% 상세 본문 높이가0이 되어1개 실패했다. 원문은
`weather232-ci-front-failure.log`로 보존했다. 경계가 없거나 읽는 중인 상태에는
패널을 줄이지 않고, 실제 도형이 있어도 확대 글자가 들어갈 높이가 부족하면
원래 상세 패널 높이를 유지한다. 기존 접근성 기대값과 CI 검사는 완화하지 않았다.
CSS import 순서 경고도 수정했다. 후속 WSL/Docker 관련27개 각각 통과,
전체 Doctor·lint·타입·의존성 검사0이다. 이27개는 이전349개에 합산하지 않는다.

Chromium156의 실제 WeatherInspector와 실제 ECharts를1440/375px 고정 자료로 렌더링해
현재/내일/작은 부가 정보/24시간 차트·관측자료 순서·갱신·info 링크의 실제 관측소 좌표
이동·범람0·pageerror0·외부 요청0·엔진 해제를 확인하고 캡처를 검토했다.
첫 fixture는 실제 complementary 역할을 region으로 기다린 시험 오류로 실패했으며
원 실패를 보존한 뒤 올바른 역할로 별도 재검증했다. 이것은 지도·Geo·인증 서버를
포함한 운영 실화면 수용이 아니다. 소유 fixture 서버와 브라우저는 정상 종료했다.
원장은 `weather-browser-20261113/results-r2.json`과
`weather-text200-fix-20261113`, `weather-text200-docker-20261113`이다.
독립 재검토의 수동 pan 중 응답 P2도 실제 회귀1FAIL로 재현했다. 경계 표시/패널 갱신은
수동 이동과 무관하게 처리하고 카메라 fit만 막도록 분리했다. 관련27개 WSL/Docker를
다시 확인했으며 기존 결과와 합산하지 않는다. 원 실패 weather-boundary-manual-pan-red.log 보존.
