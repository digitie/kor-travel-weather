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

## 후속 전체 CI의 두 실제 실패

e701d89의 CI43341365561은 frontend19PASS/1FAIL과 Python1891PASS/1FAIL이다.
본문 높이 검사를 통과한 뒤 정보 버튼을 갱신 버튼이 가려 클릭할 수 없는 실제320px/
글자200% 실패가 드러났다. 좁은 상세는 컨테이너 폭으로 버튼4개를 첫 행에 모으고
제목·info를 다음 행에 놓는다. 글자 확대 때문에 두 기온 열이 겹치는 너비에서는
현재·내일을 한 열로 보인다. 44px 버튼과 기존 Common 접기/닫기 동작은 유지하고
E2E에는 갱신/이동/info의 전체 viewport 단정을 추가했다.

새 실제 Chromium fixture1440/375/320×568(320은글자200%)는 엔진·관측소 위치 이동·
정보 버튼 클릭·overflow/pageerror/외부요청0·dispose를 확인했다.
CSS-only 수정 때 Vite의 DrvFS 감시가 예전 CSS를 계속 제공한 fixture 실패도 남겼고,
소유 서버를 정상 종료한 뒤 새 프로세스로 읽은 결과를 정본으로 사용했다.
최종 사진/원장은 weather-browser-20261113/results-r8.json이며 실제 전체 Map/Geo
운영 수용과 구분한다. 관련 프론트27PASS·타입/lint/Doctor105파일0은
weather-header-final-20261113에 있다.

Python 실패는 test_migration.py의 고정 expected0039와 실제 head0040 불일치다.
expected를0040으로 맞추고 새 대표 위치/상태 테이블의 PK·FK CASCADE·check와
마이그레이션 직후 빈 cache를 실제로 검사한다. 기존 사실 backfill·색인·안전 계약
검사는 그대로다. 전용 PostgreSQL18에서 WSL/Docker각1PASS, Ruff0이며 정확 CID·
label 확인 뒤 자기 PG만 종료·제거했다. 원장은 weather-migration-head-qa-20261113,
실패 원문 weather232-ci-python-e701-failure.log다. 기존 원 CI 실패를 성공으로 바꾸지 않는다.
추가 독립 P2는200자 위치명에서제목높이상한이없어본문이0으로밀리는경계였다.
실제Chromium320×568/글자200%에서 header440/body0을재현한뒤공통의기존3em제목
높이제한·내부스크롤을새제목wrapper에보존했다. 보완후 header183/body257 및info실제클릭을
확인했다. E2E의같은320px케이스는API fixture에서200자이름을직접받도록강화했다.
원장weather-browser-20261113/long-before.json(FAIL),long-after.json(PASS)와사진보존.

## 우선 요청 — 양 앱 상세 버튼32px 통일

사용자가 다른 후속보다 상세 버튼 축소·같은 크기를 우선하라고 지시했다.
Weather 상세의 정보·갱신·위치 이동·접기·닫기를 PC와모바일 모두32×32px,
SVG16×16px로맞췄다. 좁은폭의상단배치는유지하고일반375px에서는제목과버튼을
같은행에놓을수있는16rem컨테이너경계로맞췄다. 지도zoom/필터버튼은변경하지않았다.
Chromium156 1440/375/320×568(320글자200%)에서5개씩모두실제32×32/SVG16×16을
측정했고정보링크·갱신·실관측소위치·키보드Enter접기/펼침·엔진해제·overflow/
pageerror/외부요청0을확인했다. 캡처원장은weather-browser-20261113/results-buttons.json이다.
이것은실Inspector고정자료검증이며전체Map/Geo/운영수용은아니다.
앞의44px버튼정산은이우선요청으로대체되며공통라이브러리·모바일지도zoom의44px기준은그대로다.
정보팝업의portal닫기X도동일32×32/SVG16이며375/320추가실측을확인했다.
원장weather-browser-20261113/results-buttons-popup.json. 전체React경로타입/집중ESLint0,
서버/브라우저정상종료다. 텍스트이동링크나지도zoom44px는정사각형으로축소하지않았다.

## 전체 지도에서의 상세 본문 높이 회복

고정 Inspector만으로는 오류 안내가 함께 있는 전체 지도 치수를 충분히 검증하지 못했다.
실제 MapWorkspace·인증·제어 API를 포함한 개발 fixture에서 320×568/글자200%와
200자 위치명을 재현했다. 원 R3은 상세 본문0px, R4/R5는82px여서96px 기온을
기존90% 기준으로 볼 수 없었다. 원 실패와 캡처는 그대로 보존했다.

상세를 펼치면 지도 상태/오류를 상세 본문의 스크롤 영역에서 보여주고, 접거나
닫으면 원 지도 안내 위치에서 보여준다. 동시에 한 안내만 렌더링하며 오류 문구나
재시도 기능을 삭제하지 않았다. 접힘 상태를 기존 모델에서 제어하고 새 선택과 닫기에서
초기화하므로 같은 위치를 다시 열어도 원래처럼 펼친다. 새32px 제어 행도 기존3em
제목 높이 예산에 포함해 본문을 밀어내지 않도록 했다.

최종 개발 fixture R6은 원 추정값을 낮추지 않고 실제 두320px 시험을 모두 통과했다.
상세219px 안의 header103/body114px에서96px 기온이 보이고, 정보 팝업·접기/펼치기·
닫기·전체 지도 복귀·원 canvas 유지까지 확인했다. 원장은
weather-map-dev-r6-20261113/results.json 및 geometry-observation.json이다.
개발 기동/컴파일을 기다리기 위해 Git 밖 개발 config만 기동 대기와 expect 시간을 늘렸고,
tracked production E2E의5초 기대·90% 가시 기준은 유지했다. 임시 인증은 loopback
메모리 API만 사용했고 실제 제공자·DB·Geo·운영에는 접속하지 않았다. 이는 production
이미지 빌드나 실제 타일 수용은 아니다. 생성된 next-env의 dev 경로는 서버 종료 뒤
원래 tracked 경로로 복구했다. 브라우저 종료와 runner가 추적한 서버·시험 자식의 terminal을 확인했다.

관련27개 WSL/Docker각PASS, 타입·전체 lint·공통 의존성0, Doctor105파일 진단0이다.
중간 Root 렌더 복잡도 경고1은 안내 표시 위치 판단을 안내 컴포넌트 안으로 응집해
해결했으며 규칙 예외를 추가하지 않았다. 겹치는 시험은 합산하지 않는다.
최종 production CI와 main 반영은 별도로 정산한다.