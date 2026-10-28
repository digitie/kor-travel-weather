## 2026-10-28 — API21d9 단독 배포·첫 cold 지도 수용 완료

- [PR #190](https://github.com/digitie/kor-travel-weather/pull/190)은 main `0683431`로 머지됐다.
  실제 배포는 검증한 원 후보 `21d9ea9`의 API 한 개이며, 웹 a707·CS e393·공용 daemon과
  schema0034를 유지했다. 병합 main의0035/export/UI 추가 변경을 배포한 것은 아니다.
- 배포 뒤 별도 마커 예열 없이 첫 Firefox 실행29개가 실제 종료0으로 통과했다.
  첫60지점×3의 HTTP200·본문 완료는18.075/16.469/17.593초, 고유180지점·동시2다.
  PC1440/모바일375의 지도 면적·오버레이·실타일·단위/DTO·초점/Escape·로그아웃 쿠키0과
  소유10PID 부재·runtime 불변을 확인했다. 세 묶음 전체 경과34.074초이며 전국 전체25초 보장은 아니다.
- **W-MARKER-PREFIX-01**을 완료 목록으로 옮긴다. 이전 a707 첫 cold26초대 FAIL과 warm R17,
  각 QA 원장은 보존한다. API·CS export True/publisher STOP을 유지했고 외부 소비자 OFF는
  이번에 직접 관측하지 않았다. retention·특보 지역 정합과 다른 과제는 완료로 바꾸지 않는다.
  [실제 배포·검증 범위·증거](reports/2026-10-28-marker-prefix-deployment-acceptance.md).
- 다음은 해당 보고서의 별도 미수용 범위를 기존 담당 과제에서 검토한다. 아래 이전 항목의
  미배포·미수용 표현은 각 작성 시점의 이력이며 이 절이 이번 한정 수용의 정본이다.

## 2026-10-28 — 특보 후보와 mainbd561 통합 검증 구분

- 원21d9·150개 양환경 검증은 보존하고 별도 WT에서 mainbd561을 정상 merge한다.
  marker 함수 원문은 그대로이며 main의 export 보완27줄도 보존한다.
- main의0035/export/UI는 별도 추가 제품이다. 통합 관련 시험·CI 전체는 새 head로 회수한다.
  운영a707 API·웹/e393 CS·schema0034와 publisher/소비 flags는 이번 작업에서 바꾸지 않는다.
  원21d9 API-only 배포 여부는 Root의 별도 최종 동결 뒤 판단한다.
  [검증과 배포 범위](reports/2026-10-28-marker-alert-prefix.md#최신-main-통합과-배포-범위).

## 2026-10-28 — 특보 적격 prefix·정렬 의미 보존과 운영 실패 정산

- a707 API·웹 실제 배포 뒤 최초 cold 세 응답26초대 FAIL을 유지한다. 별도 R17 warm 진단은
  지도 면적·오버레이·타일·℃/풍속·키보드·로그아웃을 확인했지만 cold 수용이 아니다.
- 실제 한 지점 계획의 옛 fact35회 검산을 근거로 최근 적격 행이 예산을 채운 경우만 CASE로
  불필요 검산을 생략한다. LATERAL prefix, C 문자열 순서, 전체 payload와 기존 reducer를 검산한다.
- 관련 WSL150/Docker150 PASS·actual0·독립 wait0·자식 종료·연결0을 회수했다.
  schema/provider/writer/DTO/동시성/timeout·운영 설정 변경0이며 전체 지연 해결은 미입증이다.
  [변경·반례·실제 운영 구분](reports/2026-10-28-marker-alert-prefix.md).

## 2026-10-28 — 운영 API210·웹5098 공개 기본UI21 수용

- fresh f192 capability와 두 소스 리뷰 뒤 실제21개가20.241초에 통과했다. 목록1450/tile20/API4·오류0·탐색abort6별도, 로그인/로그아웃·쿠키/보호화면과 소유 C7 정리·7runtime 전후동일을 수용했다.
- 첫125 실패는 driver/21case 미실행으로 보존하며 제품실패로 확정하지 않는다. 옛UI39와현재이미지 동일성은 주장하지 않는다.
- 공유plane2는여전히unhealthy였고 mounted dagster.yaml 지문 불일치 진단만했다. 기본21PASS는마커성능·추가상호작용/모바일·전체plane건강·retention완료가 아니다. export desired STOPPED와제품/운영설정을 유지한다.
- 문서base는main d9d0(PR187 포함)이지만 수용은 API210/CS e393/web5098만이다. [실제 수용·원실패·남은범위](reports/2026-10-28-retention-catalog-and-current-source.md#후속-공개-기본-ui21-수용). 아래기록은당시이력으로보존한다.

## 2026-10-28 — 실제 마커 실패 보존·요청 예산과 특보 부분 인덱스 후속

- Root R4 API210/image1b3d 교체(actual0) 뒤에도 cold60×3의19701/30020/21016ms 중1개가504였다.
  CS e393/web5098·일정21R2S와 API endpoint의 `service_exports_enabled=True`는 유지했고 모바일/overlay 미실행·로그아웃 쿠키0을 분리해 기록했다.
- browser 동시2/in-flight 공유/실패수동retry/숨은상세조회제거와 generic 특보 고정 predicate를 보완한다.
  Backend 집중 WSL56/Docker56 actual0, frontend WSL/Docker265·타입/린트/WSL build actual0이다.
  source7/lock/vendor 보존과 소유 QA 정리를 확인했으며 원 fixture2실패도 남겼다.
- 제품이 같은1d2 WSL전체1518 PASS/actual0과 시험 한 파일만 보완한a707 Docker전체1517 PASS/
  1 SKIP/actual0·독립 wait0을 회수했다. Docker의1 SKIP는 Docker CLI가 없는 배포 계약 검사다.
- 최초 WSL27실패(실행기 spawn/PYCACHE)와 첫 Docker2실패(06:01 미공개 중기회차 fixture)를 보존했다.
  전자는 같은27건 PASS, 후자는 SDK 공개 회차 함수로만 보완해06:01/18:01의 기존2파트를 각각
  2 FAIL→2 PASS, 현재 시각2 PASS로 검산했다. 제품·SDK·timeout·assert 변경0이다.
- 소유 QA PG/nonceDB/fixture/volume/network를 정상 exit0·잔여0으로 정리했다. PR187 정확 CI·리뷰와
  배포/liveUI는 별도이며21:33 UTC R6 입력은False/None·새 배포0이다. export/publisher/소비 flags/GC·
  공용 daemon·CS 변경0이고 운영20초 지연이 해결됐다고 하지 않는다.
  [변경·검증·현재 운영 구분](reports/2026-10-28-marker-request-budget.md).

## 2026-10-28 — retention 카탈로그 실측·현재 API 소스 정합

- API는 외부 전환된main210이며 release/파일지문을 확인했다. old source guard의 DB진입 전 실패를 보존했고 current에 결박한 bounded RO1회만 정산한다. 이번 문서의 새 운영 변경은0이다.
- retention parent+16leaf coverage·valid/ready/live와 current value_id PK를 확인했다. 누락index 가설 근거는 없고 current11index/INCLUDE4가 현재 관측이다. NOT VALID FK17과 통계live899만/dead120만을 orphan·병목·정확행수로 단정하지 않는다.
- 자연7 SUCCESS/1 STARTED·8 materialization 21만facts 진척도 모두remaining/budget_exhausted다. 과거6h 정리는 미완이고 위치지연상한·통계reset 경위 미상·CPU/IO 미측정 한계를 유지한다.
- export publisher desired STOPPED를 유지한다. 과거APIe393/web5098 UI21은 당시수용이며 현재210 UI재검증이 아니다. [관측·원실패·남은수용](reports/2026-10-28-retention-catalog-and-current-source.md). 아래 기록은 각 작성시점 이력이다.

## 2026-10-28 — 특보 hash 전역 스캔 경계와 CI 시험 입력 보강

- 정확1bafa CI756 1510/3FAIL·757 1511/2FAIL 원문을 보존했다. 기존 SQL recorder 대역과23시 변조 no-op를
  실제2RED→2GREEN으로 보완했다. export 제품/설정은 변경하지 않는다.
- 통계가 있는 ownPG에서 EXISTS가 모든 fact 파티션의 hash로 바뀌는 RED를 재현했다. OFFSET0 계획 경계로
  상관 복합키 조회를 유지하며 통계 유무/제공처/custom·generic 계획을 검산한다. 관련 집중119PASS194.13초다.
  최종 정확source의 Docker/CI/독립리뷰와 운영25초 수용은 별도다. [정산](reports/2026-10-28-marker-alert-validity-query.md).

## 2026-10-28 — 실제 지도 특보 유효기간의 반복 조회 제거 후보

- 원6312 후보 의미를 유지하며 EXISTS로 별도512키 검산을 제거했다. 무필터 최적화를 기본 지도 경로로
  추정한 이전 분석 오류는 명시 정정하고 원 실패/증거를 보존한다.
- 요청 밖80행 추가로2480회 검산된 초안 반례를 재현하고 위치 ANY 조건으로2400회로 제한했다.
  원 후보의14 SELECT/검산8, 새후보6/검산0 및 파티션 pruning은 소유 PG의 합성 증거다.
- 최종 집중/CI/독립 리뷰와 운영 수용은 구분한다. [정산·미검증](reports/2026-10-28-marker-alert-validity-query.md).

## 2026-10-27 — TCP 회귀의 준비 실패 정리 보완

- 정확72 CI는 Python1505 PASS·frontend 성공이다. 독립 리뷰의 시험 정리 P2는 별도
  작업 트리에서만 보완했다. 원72 전체 두 실행의 source/DB/의존성은 고정한다.
- app/socket/bind/thread 시작/DROP 실패 mock5 RED→5 PASS, 정상 TCP+mock6 PASS를
  확인했다. ExitStack으로 UUID DB·engine·socket·worker 정리를 생성 단계마다 등록하고
  DROP 실패에도 admin.dispose를 보장한다. 제품 app·의존성·fixture/조회 조건은 동일하다.
  [실패 원문·범위·합성 검증 경계](reports/2026-10-27-marker-disconnect-admission.md).

## 2026-10-27 — 지도 취소 PR의 CI fixture 적재 단위 보완

- 정확962 CI는 Python1504 PASS/1 FAIL이었다. 조회 전9,600행 projection INSERT의
  FK 검사30초 초과 원문을 보존하고, 같은57,600행/FK/assert/조회 예산을 유지한 채
  적재 statement만960행으로 나눴다. 기존 원본 로컬 진단19.54초/보완19.51초 모두
  통과했으므로 CI 초과의 로컬 재현이나 전체 속도 개선으로 확대하지 않는다.
- ASGI/API 집중167 PASS·TCP 취소 RED→GREEN은 보존한다. 별도 disk-backed2GiB/1CPU
  PG와 두 nonce DB만 준비했으며 whole은 미실행, 운영 변경0이다.
  [원문·범위·다음 게이트](reports/2026-10-27-marker-disconnect-admission.md).

## 2026-10-27 — 기본 지도 경로 정정과 대기 중 취소의 DB 진입 방지

- 실제UI는6종류를명시한다. PR180의 `kinds=None` 최적화를 기본지도 경로로 추정한
  오류를 정정하되 기존계획·실패·전체시험 결과는 원래범위로 보존했다.
- PR178의순수 ASGI middleware와실제disconnect시험만분리해 main5098에적용했다.
  입장전/대기중취소는499·새DB작업0, 정상동시2와실행중thread의자연완료를구분한다.
- 운영추가조회·provider/job·이미지변경0. 소유PG의실제6종류7SELECT/수화와EXPLAIN ONLY,
  actualASGI RED→GREEN을저장했다. [보고서](reports/2026-10-27-marker-disconnect-admission.md).

## 2026-10-27 — Codex PR(2026-10-17 이후 머지분) 상세 리뷰 정리 PR

2026-10-17 이후 머지된 Codex PR 63개의 리뷰 결과 53건을 main `a9f54ff`(#173 포함)에서
다시 확인하고 작업(W01~W15)을 한 PR에 묶었다. #174~#177·#182·#183·#185 머지 뒤 main `9b29362`로 rebase했다.
머지·배포는 소유자 승인 뒤다.

- export 가용성: 미래 시각이 없는 위치는 예보·중기 페이지에서 빼고 stale(W01). W5 전체
  목록의 최초 미승인 anchor는 503 대신 stale, AirKorea는 W5 필수 원천에서 제외(W02, ADR-102).
  W5 fallback 날짜 창을 오늘 KST로 이동(W03). KMA 전용 예보 style은 export 경로가 없는 활성
  provider와 무관(W05, #159 회귀). 재사용 handle TTL 연장(W06), 만료 6시간 뒤 handle 정리(W07,
  ADR-103).
- publisher: 후보 단위 오류 격리·수집 중 run의 잠금 없는 판정·tick 단위 Dagster 조회 기억(W08)은
  작업 중 머지된 #176이 같은 내용을 더 넓게 구현해 이 PR에서 뺐다(#176 위로 rebase). W4 저장 scope가 catalog와
  맞지 않으면 유효 scope만 기록하고 pending 해제(W09).
- API: export 응답의 검증·직렬화·gzip을 worker thread로 옮기고 503/422 원인 분류를 로그로
  남긴다(W10). marker 취소의 `BaseHTTPMiddleware` 문제(W11)는 같은 수정을 실제 TCP 시험과 함께 담은
  #184로 넘기고 이 PR에서 뺐다(같은 middleware 중복·충돌 방지). 2026-10-28 소유자 지시(옵션 c)로 두 구현을
  비교했다: middleware는 같은 blob(`a7f6c51`)이고 #184 시험이 이 PR 시험의 상위집합(실제 Uvicorn/TCP,
  두 배치 뒤 대기 중 취소, 6종류)이라 #184(머지 `9521d16`)를 남긴다. #184 머지 뒤 main을 merge해
  journal·resume 충돌만 풀었다.
- 데이터: 강수 합계·무강수는 구간6시간 RN1이 모두 있을 때만 확정하고 0035가 기존 행을
  고친다(W04). 0022는 INVALID 인덱스 뒤 재실행 가능(W13). 결측 실황 격자의 준비 attempt를
  NO_NORMALIZED_POINTS/NOT_INGESTED로 닫는다(W14).
- UI: 필터 로딩 중 선택·상세·카메라 보존(로딩 중에는 이전 목록의 같은 위치를 선택으로 유지해
  선택 ID가 흔들리지 않는다), 기본 카메라로 시작하면 첫 선택 이동에서 확대, 예보 종류만 고르면
  현재값 조회 생략, 소스 필터 접근성 이름에 현재 선택 포함(W12).
- 배포 메모: 0035는 하루씩 commit한다. 옛 publisher가 보정 뒤 닫힌 구간을 부분 합으로 다시 쓰지
  않도록 publisher sensor를 멈춘 채 migrate→교체하거나 교체 뒤 migrate한다. W02로 W5 selection
  digest가 바뀌어 배포 전 snapshot은 W5 fallback으로 쓰이지 않는다(배포 직후 reducer가 늦으면
  첫 성공 전까지 503 가능).
- 검증은 n150 컨테이너 CI-parity와 GitHub CI 결과를 PR 본문에 남긴다. 보류·기각 항목과 이유도
  PR 본문과 [남은 작업](tasks.md)에 있다. #174·#175가 고친 코드 영역과 겹치지 않는다.

## 2026-10-27 — 현재 Dagster 재배포 불필요 확인·export 취소

- 실제 API/collector는e393(PR176), web은main5098(PR177)이다. e393→5098의Python/Dagster/build/migration 입력은 같아 #174 CommonAA·retention 개선이 이미 운영 중임을 확인했고 중복 build/교체하지 않았다.
- API/CS CommonAA19 source와Weather핵심6hash, actualschema0034/정본API12201 health/version200, 자연schedule retention SUCCESS156.153초를 새 RO로 확인했다. 7runtime·daemon·NVMe 원상태를 보존했으며 CPU/RSS 효과는 미측정이다.
- 사용자 취소에 따라 `weather_export_publisher` sensor1만 STOPPED로 변경·readback했다. 이미 시작된tick111545는 자연SKIPPED로 끝났다. 다른47-native 집합의 나머지 상태/flags/API/data는 변경0이다. 앞으로 이 sensor는 original-RUNNING 복원 예외인 **desired STOPPED**다.
- 현재source 공개UI21은 두 source 리뷰·단독browser슬롯 뒤21/21 PASS18.705초로 수용했다(목록1450/tile20/API2005, 로그아웃·쿠키삭제·로그인복귀/C7없음·7runtime불변). ERR_ABORTED11은 별도다.
- 다음 자연retention도SUCCESS154.611초·resumed true/facts41000을 확인했다. 뒤 materialization의 약4.65일/stall1은 **삭제 위치 기반 lag 상한 지표이며 실제 oldest age가 아니다**. 정시 valid_at bucket 안에서는 진척 중에도 상한이 오를 수 있어 두 표본으로 정체를 확정하지 않는다. 남은행이 있어 과거6h 물리 정리 완료는 미수용이다. [현재 source·취소·UI와 한계](reports/2026-10-27-current-dagster-and-export-cancellation.md). 아래는 각 작성 시점의 이력이며 예전 미배포/새publisher재개 표현을 현재desired state로 읽지 않는다.

## 2026-10-27 — 소유자 결정 세 건 기록과 odroid release 표지 제안

- 소유자가 "권장대로"로 세 건을 정했다. [결정](decisions.md)·[백로그](tasks.md)에 남겼다.
  (1) export 목표 일시중지 — Map `weather_state_*` schedule(locations 제외)·PinVi weather flag는 꺼 둔 채,
  KHOA 해수욕장 한 곳 catalog 좌표 수정은 보류. (2) 외부 날씨 current 약 1시간 지연은 export 재개 때
  current/forecast job 분리로 푼다(W-EXP-SPLIT-01). "run SUCCESS 뒤 승격" 규칙은 유지하고 step 단위
  판정은 하지 않는다. (3) PR #176 artifact GC는 `dry_run`으로 약 1주 관찰 뒤 `delete`(W-EXP-GC-01,
  목표 2026-11-03). `dry_run`은 guard 안 재확인을 돌리지 않으므로 그 시간은 `delete` 첫 tick에서 다시 본다.
- odroid에 현재 weather release 기록이 없어 weather173/176 배포가 엇갈린 일을 계기로
  `/srv/ktdm-releases/weather-current` 표지와 runbook 문구를
  [제안](reports/2026-10-27-odroid-weather-current-release-pointer.md)했다. **Codex 합의 필요**이며
  odroid는 바꾸지 않았다. 코드 변경 없음(문서만).

## 2026-10-27 — 공용 UI dev.9(로그인 hydration 준비 경계) 고정

- Common main `aa232ad`(PR #47, T-217)의 `git archive`에서 격리 Docker(Node 22.22.2·npm 11.19.1) fresh
  `npm ci`·build·check·UI79 뒤 `npm pack -w packages/ui`로 재생성했다. SHA256 `b738d882…b403`(27파일)으로
  Common 기록·Transport 채택 산출물과 byte 단위로 같다. package.json·lock(version·resolved·integrity)만 바꿨다.
- 공용 `LoginForm`은 SSR·최초 hydration 동안 입력·제출을 native disabled로 두고 handler 준비 뒤 연다.
  Weather 래퍼는 공개 props만 쓰므로 앱 코드 변경은 없다. 래퍼의 서버 렌더가 세 슬롯을 닫는지 SSR Vitest를
  추가했다. 기존 Playwright 로그인은 `fill()`/`click()`이 활성화를 기다리므로 그대로일 것으로 보지만 CI 밖이라
  시험 결과는 아니다. hydration 전 제출 버튼은 전역 `button:disabled`로 잠시 흐리고, 자동완성은 비활성 입력을
  채우지 않을 수 있다. tokens `0.1.0`과 dev.6~dev.8은 보존한다.
  [vendor 기록](../packages/kor-travel-weather-admin/frontend/vendor/README.md).

## 2026-10-27 — Weather173 운영 교체 완료·마커 지연 후속 진행

- 배포 merge a9f54의 세 이미지/0034를06:35UTC에 반영했다. 원래21 RUNNING/2 STOPPED·공용 daemon·타 서비스 보존,
  기존 네 인덱스 OID 보존·자연 publisher와 새 정기 current 수집 재개를 확인했다.
- 이 정산 기준 소스 main1766889의 PR #174/#175는 머지됐으며 이번 운영 a9 이미지에는
  포함되지 않았다. 아래 기존 진행 상태는 각 작성 시점의 이력이다.
- 실제 Chromium PC1440/375의 지도 면적·상세 오버레이·실제 가평14.4 값/시각은 통과했다.
  **마커 latency는 미완**이다. 후속60×3의19.506/29.884/28.324초 중 두 개가25초 상한을 넘었다.
  초기70초 수용기 실패·명시 ERR_ABORTED6개를 원본 보존하며 빠른 응답 성공으로 대체하지 않는다.
- [배포·긴 중지 구간·UI 실패와 후속](reports/2026-10-27-weather173-deployment-and-latency.md).
  다음 한 작업은 저장된 실제60IDs/시각과 query/predicate/index/조인/정렬의 병목을 좁히는 것이다.
  취소 safepoint·기존 인덱스 baseline rollback·pause 전 장기 run 대기는 별도 운영 P1이다.

## 2026-10-27 — Common aa232ad 고정과 단기예보 정리 상시 실패 수정(PR #174)

- Common pin을 `e746e4b` → main `aa232ad5f255233184de80eceaab2e9b6ccb00c9`로 바꿨다(pyproject 두 파일·
  uv.lock·health 채택 시험). 그 사이 Python 변경은 #44 `daemon_pool`의 opt-in
  `KOR_TRAVEL_DAGSTER_WORKSPACE_RELOAD_SECONDS`(60|180)뿐이며 공용 daemon 전용이라 Weather 코드 변경은 없다.
  `@kor-travel/ui` dev.9 vendoring은 이 PR 머지 뒤 별도 PR로 한다.
- odroid 읽기 전용 실측: `weather_short_forecast_retention_job`은 10-20 22:50Z 마지막 성공 뒤 상시 FAILURE
  (최근7일 FAILURE835). a164db3 커밋 메시지의 "성공 0"은 그 뒤 기간의 뜻이다. 매 batch가 부분
  인덱스 머리의 dead 약236만 항목(1020/1021/1022, 마지막 autovacuum 10-20~22)을 후보 SELECT와 EXISTS로
  두 번 지났고(머리 탐색 cold 15.3초·34,708 read), 진척이 있어도 완료가 아니면 Failure였다.
- 수정: 부분 인덱스 순서의 위치 이어 읽기(batch는 앞 마지막 키 뒤, 실행은 앞 materialization의
  `resume_*`), 잠근 행의 tableoid+ctid 삭제, lock_timeout 대기(SKIP LOCKED 제거), 잠금/timeout은 batch
  연기. [원인·동작·확인 방법](shortterm-retention.md#2026-10-27-상시-실패와-이어-읽기).
- 적대 리뷰 반영: 위치는 정책 해시·DB 이름·경계 앞섬·24시간 주기 머리 훑기로 검사한 뒤에만 쓰고
  (훑기가 시간 초과면 같은 실행이 저장 위치에서 이어 정리), dry-run은 진척이 아니다.
- 소유자 지시(job이 실패를 멈출 것) 반영: Failure는 진척0과 "24시간 넘게 뒤처진 채 지연 상한이 7회(약70분) 연속
  줄지 않음"뿐이다. 뒤처져도 줄고 있으면 SUCCESS+warning이다. 실행당 예산을 설정
  `KOR_TRAVEL_WEATHER_SHORT_FORECAST_RETENTION_BUDGET_SECONDS`(기본150초, 상한180초)로 올려 유입의 약2.5배로 했다.
- 운영 쓰기·VACUUM·DDL은 하지 않았다. 남은 한계는 pointer FK 검사의 무작위 PK 탐색 비용이다.

## 2026-10-27 — 지도 마커 배치 병목과 미확인 표시 보강 중

실제 UI 면적/상세 통과와 별도로 마커 배치504·미확인 구름 오표시를 재현했다.
0034의 현재/예보·제공처 prefix 부분 인덱스, 다중 제공처 대표 병합, 특보512키 스트리밍,
미확인 표시·지도 전용 재조회를 보강했다. 집중PG15/계약27·migration보호31·frontend259/type/lint/build·
Ruff는 통과했고 정확 후속 정확SHA 전체CI/2인 리뷰와 수집 종료 뒤 운영 수용은 진행 중이다.
[실측·실패안·인덱스 비용·배포/되돌리기 경계](reports/2026-10-27-marker-batch-and-unknown.md).

후속 독립 리뷰에서504 뒤 검색 결과0/종류해제 시 옛 재조회가 남는 경계를 두 회귀로
재현하고 조회할 위치가 있을 때만 노출하도록 보강했다. 위치 복귀 시 실제 요청도 확인했다.

후속1bda CI의1318통과/1실패는57,600행 fixture FK 적재의30초 한계였다.
측정 쿼리/timeout/FK는 바꾸지 않고 적재를10지점씩 나누고 총행 수를 검산했다.

첫 후보5ba2의 CI Python1299/frontend257은 통과했다. 리뷰의 인덱스 소유/정의·schema
충돌 보호를 별도 worktree/DB에서 보강했으며 원본 전체 실행과 운영 수집은 보존했다.

## 2026-10-27 — Weather171 배포·기존 metrics·공개 UI 수용 정산

- PR #171의 최종74482는 정확 lock CI Python1292·frontend253/type/lint/build 및 독립2인
  검토를 통과했고 같은 전체 tree의 main `95020bd5`로 병합됐다. 아래 초안/실패 기록은
  당시 상태로 보존한다.
- 2026-10-26 공식 배포에서 API·code-server·웹 세 이미지가95020으로 교체되고 migration0033을
  적용했다. API/CS는 healthy, 웹은 healthcheck 없이 running이다. 공개 UI21개는14.970초에
  통과했고 로그인/쿠키·메뉴/지도·기존 자료 조회·재조회·로그아웃/보호화면·정확 정리를 확인했다.
- 기존 CS metrics listener의 단일 GET은2241B·HTTP200/server_up1·0.066120초다. 앞선 감사도구의
  source/scrape 단계 FAIL과 구분한다. 실제 memo-hit 횟수·운영 CPU/RSS 개선·장시간 안정성은
  미측정이며 기존 합성 CPU 비교를 운영 효과로 환산하지 않는다.
- 2026-10-26 07:06Z에 원래21RUNNING/2STOPPED만 복원했다. export publisher와 native recovery는
  STOPPED를 유지하고 Map 연동 신규 활성화0·공유 daemon/타 instigator 변경0이다.
- 이 문서 작업은 저장 원장만 검산했다. 새 배포/수집/HTTP/SSH/PG 실행0이며 원본 미커밋 metrics
  파일4개는 보존했다. [범위·원장·남은 수용](reports/2026-10-27-weather171-deployment-closure.md).

## 2026-10-26 — metrics scrape의 중복 raw key 검증 제한 메모

- 최신 main5d7에서 별도 branch와 [Draft PR #171](https://github.com/digitie/kor-travel-weather/pull/171)을 만들고 설계571db9d→구현86f4c15를 작게 push했다. 원본 미커밋 metrics 파일4개는 보존했다.
- preflight 지역 set4096개/262144문자만 보존하며 초과 새 key는 원 검증을 계속한다. 새 scrape·merge-race 재시도는 새 set이다. merge·무결성·파일 격리·수집/API 계약은 그대로다.
- 새10회귀 PASS/전체Ruff PASS, 합성979×64KiB/32key endpoint fresh ABBA×2는 CPU 중앙0.135418→0.081760초/응답5152B 전체 동일이다. 운영 ARM·whole수집·RSS 개선은 수용하지 않았다.
- 로컬 기본 PG autouse 연결거부 및 구 Common/KMA import 실패는 환경 이력으로 분리했다. frontend CI는86f4에서 PASS지만 Python/최종 head CI·두 독립 리뷰는 아직 미완이다. main merge·운영 적용은 Root의 후속 범위다.
- [설계·회귀·측정과 한계](design/2026-10-26-metrics-scrape-key-validation.md). 과거 기록은 아래 원문으로 보존한다.

## 2026-10-25 — 지도 타일 회복·목록 경고 가림 P2 수정과 Common 최종 pin

- source `3554f7c`의 CI40176053743 전체 SUCCESS·Docker253/type·native Chromium 실제 API/타일375/1440 PASS는 이전 후보의 증거로 보존한다.
- 독립 Popper·James가 최초 load 이후 타일503→회복 때 오류가 남는 P2를 각각 지적했다.
  운영 키 없이 실제 MapLibre의 타일 요청에 PNG/503을 주입해 양 브라우저에서 재현했다.
  동일 키의 성공만 해제한 중간 후보도 실패 범위를 벗어난 정상 pan 뒤 경고가 남아 양 브라우저로 다시 재현했다.
  실제 loaded 이벤트와 공개 `Map.coveringTiles()`의 현재 표시 범위를 idle·style/tiles 완료와 함께 판정한다.
  현재 화면에 실패 타일이 없을 때만 지도 오류를 지우며 private 필드·새 네트워크 wrapper는 사용하지 않는다.
  MapLibre는 errored 타일도 load/idle 완료로 세므로 최초 load의 무조건 해제를 제거하고, 지속503·최초503과 날씨 요청 오류는 보존한다.
- James의 `/providers`503 목록 경고 가림도 실제 앱 hit-test로 재현했다. 첫행 중앙이 경고에 막히던 상태를
  정상 flow의 경고+남은 높이의 단일 목록 스크롤로 바꿨다. 첫행 클릭·상세·닫기·경고 접근을 양 브라우저로 확인했다.
- Common 제품 `a74ac808752fd6d6062e529909fd0dd656724f08`/dev.8 pack SHA256
  `71d9f6ce8c178e3c66a47593a70a7fd9274938587ef9d96c1c7023673370059f`를 vendor/lock/출처에 고정했다.
  공용 boolean 빈 슬롯 보강을 포함하며 Weather는 기존 detail=null 계약을 보존한다.
- WSL 타입/lint(기존 경고1)·19파일253개·package build·Chromium/Firefox 기존32+목록 회귀2=34개(2.6분),
  실제 엔진의 동일 타일/정상 다른 범위 pan·zoom 회복/지속503/최초503 주입10개(1.3분)와 diff 확인 PASS다.
  안내는 `지도 표시 오류 · 목록 보기`로 줄여 일부 오류를 전체 지도 불가로 설명하지 않는다. 운영 VWorld 서비스 수용과 구분한다.
  PID·명령·CWD 소유를 확인한 전용24135 서버만 중지하고 증거·소스는 보존했다.
  [재현·최종 검증](reports/2026-10-25-weather-map-workspace.md). 새 source CI·Docker·독립 재리뷰·머지는 [PR #170](https://github.com/digitie/kor-travel-weather/pull/170)이 정본이며 아직 별도다.

## 2026-10-25 — 공용 지도 작업면 높이·필터/상세 슬롯·확장 로컬 수용

- `/weather` shell만 유한한 남은 높이를 공용 작업면으로 전달한다. 모바일 메뉴 한 줄, 기본 필터 접기,
  공용 summary/detail header/body와 목록 단일 스크롤을 연결하고 `70dvh`/최소 `28rem` 중복 높이를 제거했다.
- 글자200%의 실제 지도 부족·상세 body0, Chromium resize 입력 초점, 숨은 출처 trigger와 남은 portal 메뉴를 재현했다.
  공용 명시 확장·header/body 예약·초점 보강과 앱 소유 출처 메뉴 닫기 책임을 분리해 해결했다.
- Common `20953b`/정규 pack `65480d…`에서 타입/lint(기존 경고1)·Vitest253개·핵심 UI10개 PASS다.
  200% 확장 stage285px·상세 body122px에서 기온/특보/최신을 스크롤로 읽고 닫기44·중첩 Escape·원복 초점을 확인했다.
  출처 메뉴 기준점이 숨겨지면 닫고, 일반 outside 검색 클릭의 초점은 유지한다.
- 후속으로 200% zoom72px와 상세27px 겹침, resize 중 이미 옮긴 외부 focus를 메뉴 닫기가 빼앗는 경계를 재현했다.
  zoom group 아이콘의 최대/터치44px와 외부 focus 보존 guard를 적용했다. 공용 `f15511a`/pack `866aca…`를 최종 고정하고
  WSL 타입/lint(기존 경고1)·Vitest253·package build·Chromium/Firefox UI32개(2.2분)를 통과했다.
  375/320 시작233px, PC 아래24px, 200% 확장 body122px·zoom 비겹침과 양방향 resize focus·중첩 Escape를 확인했다.
- 여섯 날씨 종류·제공처/LIMIT 요청·캐시 세대·abort·시각/원본값·인증은 보존한다. 백엔드·SDK·DB·운영 변경은 없다.
  [범위·후보별 치수·검증 경계](reports/2026-10-25-weather-map-workspace.md).
  최종 Docker·CI·독립 리뷰·머지는 [PR #170](https://github.com/digitie/kor-travel-weather/pull/170)에서 정산한다.

## 2026-10-25 — 작은 UI 첫 묶음: Weather 톤·보조 안내·헤더 밀도

- 독립 James는 runtime 후보 `cef6f46355babda2e25210a63344e8e64dfc93fc`를 PASS로 검토했다.
  Popper는 metric 수를 원본 응답 수처럼 부른 `원본 레코드 N개`의 의미 불일치를 P2로 지적했다.
  여러 metric의 같은 `source_record_key` 공유를 보존하며 개수 없는 `측정값별 원본 키`로 바로잡았다.
  공유 키·미제공 키·최근 측정값 실제 개수와 내부 스크롤을 fixture로 검증했다. WSL 타입·같은 브라우저 UI8개 재검증 PASS(36.4초)다.
  수정 후 최종 source `415bba5edc53550baf2bc5f937a91a3dacd56c5e`는 두 독립 리뷰와 CI를 통과해
  main `931502e8c2724c4a471326685d6df10d972438d3`로 머지됐다.
  후속 CI·독립 재검토·최종 머지의 정본은 [PR #169](https://github.com/digitie/kor-travel-weather/pull/169)다.
- 공용 `--kt-*` 색상·폰트·초점·그림자를 Weather navy/dark 토큰에 연결했다. Common 패키지는 dev.7 유지다.
- 지도 제목/상세의 긴 안내·위치 ID·좌표·출처·원본 키를 제목 옆 `i`로 옮겼다. 특보·수집 시각·오래됨·오류는 바로 보인다.
  수집 시각을 조회 완료 시각으로 대체하지 않는다. `/weather`의 헤더 카드/이중 여백만 compact로 줄였고 coarse 버튼44px를 보장했다.
- WSL 타입/lint(기존 경고1)·Vitest253개·Chromium/Firefox PC/모바일 UI8개 PASS.
  [기록과 잔여 경계](reports/2026-10-25-weather-ui-compact.md). 최종 CI·독립 리뷰·머지는 [PR #169](https://github.com/digitie/kor-travel-weather/pull/169)의 결과를 따른다.
- 지도 가용 높이·모바일 시작 목표·시간별 우선 상세/API는 미완이며 운영 타일/배포 성공으로 세지 않는다. 운영 빌드·배포는 수행하지 않았다.

## 2026-10-25 — 웹·Android/iOS 조사와 Weather 지도 후속 설계

- 날씨알리미·Windy·지도/유가·공항 자료의 공식 설명·공개 화면·개별 후기를 조사해
  [이용 상황·취향·현재 소스 대조](research/2026-10-25-weather-ux.md)를 기록했다. native 앱 설치·실행은 하지 않았다.
- Map 공용 외곽/높이·Weather 토큰·특보/시간별 우선 상세·기존 API 보완과 상호 링크를
  [설계·작업·수용 시나리오](design/2026-10-25-weather-map-improvements.md)로 정리했다.
- 이미 있는 overlay·종류/출처 API를 미구현이라고 쓰지 않았고 운영 지도는 로그인 이후 미측정으로 남겼다.
  기존 admin 메뉴/조회 구조를 유지하며 매 화면 요청/응답 검사 패널을 추가하지 않는다.
- 문서 전용이며 기능·수집/DB·운영·빌드/배포는 변경하지 않는다. 사용자 지시의 두 차례 정리·적대 리뷰 1명·
  CI 대기 없는 문서 머지 절차를 따른다. Popper의 목적지 검색 P2를 보완하고 같은 리뷰어 재검토 PASS를 받았다.
  [검토 기록](reports/2026-10-25-weather-design-review.md), [Weather #168](https://github.com/digitie/kor-travel-weather/pull/168).
  문서 작성/검토만 완료 처리하고 구현·운영 수용은 백로그로 남겼다.

## 2026-10-24 — 공용 지도 작업면 채택 후보

최신 main106a31e의 보안 의존성을 통합했다. Popper가 현재 맑음20도와 미래 비10도가 강수20도로
섞이는 P2를 재현해 현재값 우선·예보 대상 시각 일치로 수정했다. WSL 타입/lint·253개·빌드와
375/1440px Chromium2개 PASS, 사용자 DB4개는 보존했다. 최종 CI/후보 독립 재확인은 별도다.
첫 Python CI의1280통과/2실패는 고정 발표에 now()를 섞은18시간 만료 fixture였다. 테스트 발표만 현재 유효한
구간으로 맞추고 격리PG 관련89개/Ruff를 통과했다. 운영 유효기간 제한과 UI 실행 코드는 바꾸지 않았다.
ee82c26 전체CI 통과 뒤 James가 전체 종류 선택의 예보 마커 누락과 catalog 대기/실패의 정상empty 혼동을 지적했다.
마커6종 명시 요청·empty 조건을 수정하고 타입/lint·253개·빌드·실제 UI2개(전체종류/지연/502/정상empty 포함)를 통과했다.

특보·실황·초단기·단기·중기·관측 종류 필터를 제공한다. 제공처와 종류는 같은 저장 행에서 LIMIT 전에 선택한다. 특보는 observed와 분리한다. 마커는 metric별 대표를 보존하고 미래 예보는 별도 forecast 배열로 반환한다. OpenAPI와 TypeScript client를 함께 갱신했다. KMA 중기 문자열·강수 우선순위를 보강하고 상세는 지도 위에서 닫기/Escape·포커스 반환을 지원한다.

WSL 타입·frontend251·종류/제공처 PostgreSQL20 PASS. Chromium375/1440px 필터30px·176px·PC한줄·실제좌표/줌·종류요청·상세배치·선택위치이동·Escape/포커스·가로 overflow2 PASS. 독립 Popper 지적을 반영했다. 전체 Python·CI·최종 소비자 통합 gate는 아직 정산 전이다. [정산](reports/2026-10-24-shared-map-ui.md). 다음은 후보 CI/최종 소비자 검증이며 자체 운영 빌드·배포는 하지 않는다.
## 2026-10-24 — 여섯 프로젝트 npm 보안·버전 정합

- 이 저장소의 기존 프론트엔드/공용 테스트 243개와 타입·린트·빌드 PASS. [버전·예외·검증 범위](npm-security-alignment-2026-10-24.md).
- 직접/전이 npm 의존성과 잠금 파일을 갱신하고 여섯 프로젝트의 공통 버전을 맞췄다.
- 호환성 예외·미수정 상류 권고·검증 범위는 [정합 기록](npm-security-alignment-2026-10-24.md)을 따른다.
- 소스·잠금 파일 정합과 WSL 검증을 완료했다. 최종 CI·두 독립 확인·N150 후보 UI 게이트와 머지 상태는 [PR](https://github.com/digitie/kor-travel-weather/pull/166)에서 정산하며 통과 확인 전 머지하지 않는다.

## 2026-10-24 — Next.js 16.4.3 보안 의존성 갱신

- Next.js 15에서 `16.4.3`으로 정확히 고정하고 잠금 파일을 재생성했다. 사용하는 `eslint-config-next`도 `16.4.3`으로 맞췄다.
- ESLint 16 flat config로 전환했다. React Compiler 전용 규칙 4개는 기존 동작을 보존하도록 별도 도입까지 끄고, 기존 Hooks 규칙은 유지한다. lint는 기존 전체 문서 로그아웃 이동에 대한 경고 1건과 종료 코드 0이다.
- WSL Linux Node 22.22.2에서 프론트엔드 기존 테스트 243건과 lint·type-check·build PASS. 빌드가 생성한 타입 선언과 필요한 JSX 설정을 반영했다.
- 같은 시점 `npm audit` 전체 보고: 총 9→6건, critical 3→2건. `next` 항목은 제거됐지만 다른 패키지 취약점은 남아 있다. 강제 자동 수정이나 요청 외 major 의존성 갱신은 하지 않았다.
- James(프론트/UI)·Popper(빌드/운영) 독립 사전 리뷰에서 이 저장소의 추가 P0/P1은 없었다. 실제 서비스 UI와 CI 이후 머지 게이트를 대체하지 않는다.
- 기존 작업 트리는 보존하고 Linux 격리 작업 공간의 `codex/next-16.4.3-security`에서 변경했다. CI·운영 배포·실제 서비스 UI 수용은 아직 별도이며, 이 기록을 운영 반영 완료로 세지 않는다.

## 2026-10-24 — Map 서비스 API 문서 독립 정합성 감사

- producer W1~W5와 #164 source vector8,192/catalog·cohort5,000/inventory20,000을 구분하고 인계의 Weather meta cursor·페이지4MiB·W4 단일 응답을 현재 구현에 맞췄다. 현재 tasks/resume/초기 설계 상태와 W5 재투영4세대 batch·최대256세대/원본 단기예보6시간 범위도 정렬했다. API/DTO/OpenAPI 생성물·운영은 변경하지 않는다.
- 구현과 별개인 flag/bootstrap·Map 소비·공유 artifact/GC·ARM 비용 수용은 계속 미완이다. 문서 전용 PR이며 기능 시험/운영 검증을 새로 통과했다고 주장하지 않는다. 고정 DTO로 예시8개를 검증하고 OpenAPI 경로/쿼리/상태와 문서 전용 경계까지 오프라인13개 확인을 통과했다(Weather·Transport 합계, HTTP/DB0).

## 2026-10-24 — 승인 source 용량과 제한된 메타데이터 PR164

- source vector8,192와 catalog/page/cohort5,000을 분리하고 refs/불변 계획을64행
  server cursor·JSON 예산으로 읽는다. 위치별 큰 descriptor 반복을 제거한다.
- capacity 초과는 별도 불변 stale handle로 보존하고 정상 freeze 때 회복한다.
  후보 PG65PASS/208.46초, main통합24PASS/44.24초, fastpath19PASS/33.10초(중복 합산 안함).
- [격리 크기·비용/한계](reports/2026-10-23-export-capacity.md): 중기 공유 계획의 메모리
  저감과 작은 계획 다수의 검사 CPU 비용 증가를 함께 기록했다. 운영/ARM 성능 주장이 아니다.
- PR163의 source별 W5 coverage·선택 건강도와 기존 guard/pool 반환을 보존했다.
  원천 호출·운영/flag·schema·GC 변경0. 정확 CI·리뷰·HTTP/UI는 PR에서 정산한다.
- 722a42d 정확CI는1269PASS/일간 경합 시험1FAIL이었다. guard가 CAS까지 확장된 뒤
  늦어진 시험 barrier만 실제 read 소진 후 SH 내부/CAS 전으로 이동했고 CAS 단언은 유지했다.
  새 소유 PG에서 동일 회귀5회 연속 PASS. 제품 변경0이며 새 정확CI 결과는 PR에서 정산한다.

## 2026-10-23 — 중기예보 두 원천 grouped reader

- 같은 발표의 육상·기온을 위치별 배열로 묶는 별도 reader를 추가한다. 전체 raw
  scope와 frozen cursor/publication watermark를 보존하고 작은 두 chunk만 cache한다.
- 순수16건+실제 새 PG5건21PASS(33.64초), 실제 앱 인증·304/gzip·stale/old cursor·
  서명/선택·TTL·기본 OFF3건 PASS. 선행 통합 뒤 관련29건 PASS/전체 Ruff PASS.
- 제품 `ba70683e`에서 두 독립 최종 리뷰 PASS/OPEN0, 중기 실제 TCP19건과
  전체 제공처 TCP11건+동일 API의 production frontend UI11건 PASS. 정확 CI와
  main 통합 수용은 PR에 남긴다. 운영 flag OFF·실원천 호출0을 유지했다.
- 같은 의미의 재수집에도 artifact의 원래 known_at을 유지한다. 새 수집 clock과
  publication watermark는 각각 meta.source_collected_at과 published_at/next_since다.
- 정본: [중기 grouped reader](reports/2026-10-23-mid-grouped-reader.md).

## 2026-10-23 — export 실행 설정과 공유 파일 배선

- API·code-server에 동일한 기본 OFF flag와 공유 named volume 아래 artifact 경로를
  전달한다. 컨테이너별 파일 계층에 분리되어 승인 파일을 읽지 못하는 배포 누락을 막는다.
- 기존 배포 계약 21개와 실제 독립 컨테이너 두 개의 작성→교체→guard/파일 읽기,
  디렉터리 0700·lock 0600·파일 0400을 확인했다. [범위와 운영 gate](reports/2026-10-23-export-runtime-volume.md).
- 운영 flag·daemon·instigator·배포는 변경하지 않았다. NVMe mountpoint·GC/pin·전체
  source·ARM 비용·실제 Map consumer 수용은 활성화 전 남은 gate다.

## 2026-10-23 — W4 native 수집·복구·실제 일정 통합 PR151

- [설계·수용 경계](reports/2026-10-23-export-status-integration.md): source 별칭 전 실제
  fetch 시각, 원자 native 요청 계획, terminal CAS·중단 회수의 상태 투영을 연결한다.
- 독립 리뷰에서 찾은 pending INSERT 실패와 계획 setter 실패 경로는 native INSERT와
  terminal UPDATE의 durable flag로 보강한다. 지역 bulk fetch 실패도 추적한다.
- 실제 tick의 중복 억제/config/tags는 유지하고 다음 일정을 작은 상태표에 기록한다.
  W4 REST는 인증/weak ETag/gzip과 작은 전체 목록을 제공한다. 기본 OFF·운영 미배포이며
  수정 후 테스트·적대 리뷰 최종 폐쇄·CI·실제 HTTP/UI 수용이 남아 있다.

## 2026-10-23 — 성공 head/실제 collector opt-in PR143·144 수용

- PR143 제품43fa8ae→main476c9f11: exactarchive PG812/집중71·CI·frontend243/type·독립2리뷰·격리API3/UI11 PASS. source clock P1을폐쇄했다. [수용/미완](reports/2026-10-23-map-export-success-heads.md).
- PR144 제품68dd7a5→main089bdc32: exactCI전체PG/frontend·집중33·두리뷰·격리API3/UI11 PASS. 51location의원본50/1잠금상한을보존했다. [수용/미완](reports/2026-10-23-map-export-collector.md).
- 기본OFF/API503·운영미배포를유지한다. strict8category·실제W1/W2 frozen reader는PR145 진행중이며전체provider/W3/W4/W5·비용/GC·전국운영수용은미완이다.

## 2026-10-23 — W1/W2 승인 KMA 실황 frozen reader 구현 중

- [scope·cursor·미완 경계](reports/2026-10-23-map-export-frozen-reader.md): 실제 REST200/304·gzip·고정generation vector·TTL410·cursor422와 strict8category공개정책을 구현 중이다. 기본OFF이며전체provider/W3/W4/W5·비용gate·운영수용은남는다.

## 2026-10-23 — 실제 KMA 실황 collector opt-in 배선

- [실제 collector·독립 publisher와 미완](reports/2026-10-23-map-export-collector.md): 실제 KmaClient→기존 정상화→단일 grid 전체 capture→exact native/Dagster 성공 publisher를 구현했다. 집중PG32 PASS, 기본 OFF·API503 유지. 전국 sensor/reader/W3/W5·비용 gate·운영 수용은 남는다.

## 2026-10-23 — Map export 단일 KMA 실황 성공 head 구현 중

- [3B 범위·안전장치·미완](reports/2026-10-23-map-export-success-heads.md): 불변 요청계획, public pagination/정규화 증거, 성공 generation CAS, 같은 내용 파일 재사용을 구현했다. 검증·두 적대 리뷰·CI 대기이며 기존 collector OFF·API503이다. 실제 W1~W5/전국 완결·비용 gate는 계속 남는다.

## 2026-10-23 — KMA 페이지 완결성 provider 계약 수용

- KMA PR32의 검증된 public pagination receipt를 고정하고 lineage에 보존한다. [범위·키 전환·미완](reports/2026-10-23-kma-pagination-contract.md)을 따른다. Weather PR141은 전체PG788·정확CI788/frontend243·독립2리뷰·격리API3/UI11 수용 후 머지됐다. export OFF/503·운영 미배포 상태다.

## 2026-10-23 — Map export 내부 완결 검증 PR139 머지

- nonempty 내부 lifecycle 범위의 예상계획·source별 admitted point·scope owner epoch·정확 native/Dagster 성공 검증을 머지했다(main4e92b241). 최종 두 리뷰·CI Python785/frontend PASS, 집중PG47·local frontend243/typecheck PASS다.
- 전체 로컬782는 초기adb86da baseline이며 최종수정47회귀/정확HEAD CI와 구분한다. 격리실제API3/UI11 PASS는503/auth/기존UI 수용이고 실제 데이터 공개·운영 배포가 아니다. [상세 증거·미완 경계](reports/2026-10-23-map-export-plan-barrier.md).
- 다음은 정상0 완결/실제 adapter·collector 통합과 3B 성공head/CAS다. 비용/frozen/GC·W1/W2/W4 실제값·W3/W5·Map 운영 수용은 남아 있다. collector OFF·API503·daemon RUNNING 정책을 보존한다.

## 2026-10-23 — Map export 단계3A 구현 중

- 예상chunk/source 완결·scope epoch fencing·정확한native/Dagster terminal 성공 검증을 추가한다. [구현/검증/미완경계](reports/2026-10-23-map-export-plan-barrier.md). 집중PG40 PASS, migration/full/CI/2리뷰는 진행 중이다.
- 기존 collector opt-in OFF·API503을 유지한다. 실제head/COW/frozen API·W5가 끝났다는 의미가 아니다. 운영 변경은 없다.

## 2026-10-23 — 남은 API 작업 독립 감사

- [W1~W5 요구사항·코드·PR·검증·운영 구분](audits/2026-10-23-map-api-remaining.md)을 작성했다. 실제 데이터 공개는 미완이며 계약/receipt 머지를 전체API 완료로 세지 않는다.
- 현재 daemon 사용자정책은RUNNING이다. 과거OFF는 이력으로 보존하며, 문서정산만 수행하고 운영은 변경하지 않았다. 다음은3A→3B→비용/frozen gate→데이터 공개이며 W5는 성공관측projection 뒤다.

## 2026-10-23 — Map API 단계2 기반 머지, 실제 공개는 후속

- 단계1 PR135 main9d75b64와 단계2 PR136 main91801d4를 머지했다. PR136 최종9bd5f21의 두 독립 적대 리뷰·CI PASS, WSL 전체Python756 PASS1150초(시작6d764da)+수정집중24 PASS29.29초, frontend243/type-check PASS, 격리실제API3범주/UI11 PASS다. 운영 배포/collector opt-in/실제 W1~W5 데이터 공개는 아직 아니다.
- 잠금 실패표시의 원본 rollback/P1·후속 무한대기/P2·run미지정 원천혼입/P2·FIFO open 대기/P2를 실제PG/소유child 회귀와 함께 해소했다. [단계2 기록](reports/2026-10-23-map-export-receipts.md).
- 다음은 [단계3A 계획·완결·terminal 검증, 3B 성공head](design/map-export-promotion-plan.md). COW 합산비용 gate·frozen pagination/GC·W5는 미완이다. 기존현재pointer의 실패값을 성공값처럼 내보내지 않는다.
## 2026-10-23 — Map API 단계1 머지·단계2 receipt 기반 구현 중

- 계약·인증 경계 PR135는 두 독립 리뷰·CI·전체Python738/frontend243·격리실제API3범주/UI11 뒤 main9d75b64로 머지했다. 운영 배포는 아직 하지 않았다.
- 단계2 opt-in receipt·canonical 불변 후보를 구현했다. 신규 insert0/CAS0도 persisted정본으로 기록하고, 이번 재관측 clock을 최초 known_at과 분리한다. 파일은 원본commit/pool반환 후 저장하며 파일실패가 기존 원본 성공을 취소하지 않는다. 전용PG집중18 PASS.
- [검증·미완 범위](reports/2026-10-23-map-export-receipts.md). 실제 collector 활성화·scope완결/terminal성공 승격·실제API/ETag/gzip·W5는 아직 미완이며 API는503 계약을 유지한다.


## 2026-10-23 — Map service API 계약·인증 경계 구현 시작

- W-MAP-01: `/v1/service/exports/locations,current,status`의 DTO/OpenAPI/TS와 기존 `require_admin`/`x-admin-token` 인증을 추가했다. 성공 head가 아직 없어 인증 후503 `EXPORT_NOT_READY`이며 빈200·304를 반환하지 않는다. DB/provider 조회도 하지 않는다.
- 웹 catchall은 service 경로와 URL 재해석이 가능한 dot/separator/percent 경로를 토큰 주입·URL 생성 전에404로 차단한다. 기존 정규 weather/admin 전달과 query를 유지한다. 프록시 우회18개 재현 뒤 수정했고 집중 UI53개·API11개가 통과했다.
- 전체 Python738/frontend243·타입/build/Ruff·CI·독립2리뷰·격리 실제API3범주/UI11개가 통과했다. 운영 배포는 아직 하지 않았다. [검증 근거](reports/2026-10-23-map-export-contracts.md). W1/W2 실제 데이터·W4 상태값 공개, 성공 receipt/승격·ETag/gzip/cursor snapshot·W3/W5는 아직 구현 완료가 아니다. 다음은 W-MAP-02 기반과 Transport T5 guard다.

# Weather 작업 기록




## 2026-10-23 Map export 구현 준비의 프록시·증거 경계 보강 — 문서만

- 최신 main `d028ea72e440b36a44744c304264e20c9ad1f0f0`에서 후속 문서 branch를 만들었다.
  기존 catchall 프록시가 모든 경로에 서버 관리자 token을 주입하는 코드를 읽고 service prefix의
  명시 차단과 upstream fetch 0회 수용 조건을 설계에 추가했다. guard 구현은 아직 하지 않았다.
- accepted hook은 canonical 변환 뒤 `candidates`의 False replay까지 포함하며 원래 fact clock과
  이번 실제 성공 수집 시각을 분리하도록 명시했다. source 연결만으로 point 완결을 증명하지 않는다.
- 계약/프록시→receipt→종료 후 성공 승격→실제 조회 공개의 4단계와 W-MAP-01/02/04 의존을
  맞췄다. W5 선택은 완료지만 구현은 미착수이며 COW 실측 gate를 유지한다.
- 코드·schema·운영 변경이나 테스트/CI 실행은 없다. 문서 PR의 2인 리뷰 후 CI 검토 없이 머지
  여부를 판단하며, 후속 기능 PR의 정상 검증·배포 게이트는 유지한다.


## 2026-10-23 W5 관측 범위 사용자 선택 완료 — 구현은 미착수

- 사용자가 “기상청 실황도 관측으로 포함(권장)”을 승인했다. W-MAP-03은 **선택 문서화만 완료**로
  닫고, 기존 OBSERVED + 실제 관측 기반 KMA NOWCAST를 설계에 고정했다.
- main `b31c33d4d5188810d52dd102d4e59346b4aacc33`의 `_nowcast_value`를 확인했다.
  provider `python-kma-api`, dataset/weather_domain `kma_ultra_short_nowcast`, source_metric_key와
  category 동일, payload `obsr_value`(원천 `obsrValue`), observed_at/target_at, typed unit·기존
  결측/범위 검증을 조건으로 삼는다. W5 지표 T1H/RN1/REH/WSD/PTY를 구체화했다.
- 일반 미래 예보·KMA forecast dataset·외부 예측 NOWCAST를 무차별 포함하지 않는다. 실황에
  없는 SKY는 예보에서 채우지 않으며 강수 interval/coverage·reset·경계 분해의 보수 규약은 유지한다.
- W-MAP-04는 성공 receipt/projection W-MAP-02가 계속 선행이며 구현·초기 backfill·배포는
  미착수다. COW artifact도 실측 gate 전 미채택이다. 아래 설계 보존 절의 pending은 당시 상태다.
- 수정 범위는 설계/tasks/journal/resume 문서뿐이다. `[skip ci]`, CI 실행/검토/대기 없이 문서 PR의
  독립 리뷰 2인을 거쳐 머지 여부를 판단한다. 코드/schema/운영 변경과 기능 완료 주장은 없다.


## 2026-10-23 ADR-114 연계 서비스 export 설계 보존 — proposed

- Weather main `12c3a4adfcd7a030bedaf5346371a5684656ea71`에서 문서 branch를 만들고
  [설계 정본](design/map-service-exports.md)을 보존했다. 코드·schema·운영 변경과 구현 착수는 없다.
- Weather의 기존 인증은 require_admin/x-admin-token/AdminToken이며 새 service route에도 재사용한다.
  Transport 기존 header/CIDR/path·#103과 #104의 병합 순서를 구분하고 소유권을 분리했다.
- 독립 초안 적대 리뷰 2인의 지적을 반영했다: failed chunk 공개 차단, 성공 재관측 0건/alias 수용,
  성공 scope barrier·checkpoint vector·상태 집계, 응답시점 미래/weak ETag, TTL과 durable checkpoint,
  collector+publisher+PG 합산 비용 gate, 강수 interval 증거와 temporal partial/준비 상태 분리.
- 당시 판정은 조건부 PASS였고 W5 관측 범위는 사용자 답변 대기였다. 위 후속 선택으로
  관측 범위 문서화는 완료했으며 기능 구현은 여전히 미착수다. COW artifact 채택은 실측 gate를 통과해야 한다. 문서 머지가 구현 승인은 아니다.
- Weather W-MAP-01~05를 미착수로 등록했다. Transport T1~T5는 외부 연계 설계이며 이 저장소의
  구현/완료로 세지 않는다. 문서 PR은 `[skip ci]`, CI 실행/검토/대기 없이 독립 2인 문서 리뷰 후 판단한다.
  후속 기능 PR은 정상 CI·2인 리뷰·Map 응답 예시 전달·live E2E/API/UI와 배포 수용을 거쳐야 한다.


## 2026-10-21 외부 provider 긴 sweep run group 분리

- odroid 공용 Dagster는 CPU 포화로 `kortravelweather/run_group=external_weather`를 동시 1개로
  묶었다. 그 결과 2~4초에 끝나는 run(weatherstack·weatherbit·accuweather·wttr_in·tomorrow_io·
  visual_crossing)이 73~90분짜리 `open_meteo_weather_job` 뒤에서 최대 한 시간 기다렸다.
- 같은 태그 키에 값 `external_weather_slow`를 추가했다. 소속은 실행 측정이 아니라 코드의 요청
  수로 명시한다(`SLOW_EXTERNAL_PROVIDER_KEYS`): `open_meteo` 380곳 × 2 dataset = 760 요청,
  `openweathermap` 350 × 2 = 700 요청 + 1.25초 간격, `weatherapi` 150 × 2 = 300 요청. weatherapi도
  같은 종류의 sweep이라 fast에 두면 포화 CPU에서 sweep 둘이 겹친다. phase가 +7분으로 가장 앞서고
  (관측 약 9분), 늦어지면 openweathermap이 그 뒤에서 기다린다; 세 sweep 관측 합은 3시간 주기 안이다.
  나머지는 기본 location 상한이 없어 기본 설정에서 꺼져 있거나 credential 없이 skip하므로 빠르다.
  태그는 정의 시점 값이라 admin credential만으로 켜도 fast에 남는다 — 켜기 전에 이 집합에 넣는
  규칙을 runbook에 적었다.
- `deploy/dagster.yaml`에 `external_weather_slow` `limit: 1`을 추가했다(기존 `external_weather` 3은
  그대로). 공용 instance 설정은 Manager 쪽에서 같은 항목을 넣어야 효과가 난다. max_runtime 등 다른
  태그는 바꾸지 않았다. 배포는 하지 않았다.

## 2026-10-21 회수 sensor tick 예산·정체 경보(Map ADR-112 교훈)

- Common pin을 main `e746e4b81b9b62dd1a74ca90ba0a1204951c2885`로 바꿨다. 이전 pin `89291f3`은
  common main의 조상이 아니었다(squash 병합 전 브랜치 커밋). src 차이는 추가뿐이다
  (daemon pool·pg_errors·stall·`reconciliation_sensor`의 `budget_seconds`/`on_stall`).
- `weather_sync_recovery`는 tick마다 engine을 새로 만들고, 100행 page를 끝까지 돌며 행마다
  Dagster run을 조회했고, 예산·cursor·정체 경보가 없었다. 이제 새 모듈
  `kortravelweather_dagster.sync_recovery`가 `budget_seconds=40`·`on_stall`로 등록한다.
  keyset 순회는 예산 안에서 멈추고 위치를 code-server 메모리에 두어 다음 tick에 이어 간다
  (끝에 닿으면 처음부터; 위치를 잃어도 처음부터 돌 뿐이라 빠지는 행이 없다). engine은 DSN마다
  한 번 만들어 재사용한다. 트랜잭션별 lock 5초·statement 10초는 그대로다.
- 정체 판정: 두 회수 뒤에도 heartbeat가 lease(3시간)보다 오래된 running 행이 남으면
  `SyncRunsStuck`. `deploy/dagster.yaml`의 18시간 정체(프로세스가 살아 멈춘 run 10개가
  `max_concurrent_runs`를 채움)는 예전 sensor에서 매 tick "0건 회수" 성공이었다. 지금은
  heartbeat 정지 약 3시간 15분 뒤 `weather_sync_recovery.stalled` 오류 로그와
  `ktw_sync_recovery_stalled=1`(규칙 `KorTravelWeatherSyncRecoveryStalled`)을 남긴다. DB 장애·
  예산 초과도 같은 시계로 15분 뒤 경보한다. 실제 DB·실제 sensor 재현 시험이 고정한다.
- 운영 배포·daemon 재개는 하지 않았다(daemon OFF 유지).

## 2026-10-18 영속 주소 예약 후속 검토 완료

최종 제품7f549f1의 두 독립 SOURCE PASS·CI와 UI/지연 재시도 결과를
[후속 종결 기록](reviews/2026-10-18-native-address-final.md)에 보존했다.
Common41은 머지됐으며 공용 호스트·소비자 운영 배포는 후속이다. daemon OFF를 유지한다.
아래는 각 구현 시점의 이력이며 현재 결과를 소급 적용하지 않는다.


## 2026-10-18 영속 예약의 잠금 연결 장애 보강

현재 Common pin은 a68d0495acc38906c31b95b1d0db163bd8f96f5b다.
동일 요청 ID와 NOT_STARTED 제출 복구를 유지하며, PostgreSQL 잠금 연결이 끊긴 뒤
늦은 제출이 STARTED를 QUEUED로 되돌리지 않도록 native run 저장도 같은 연결에 묶는다.
이전 23e4775의 동시 제출 문제와 2704830의 잠금 연결 소실 반례는 이력으로 보존한다.
새 pin에서 실제 native 지연 요청·재시도 시험은 앱별 1건 통과했다. 전체 Python·UI
회귀와 두 독립 최종 리뷰는 진행 중이며, 이 기록은 운영 수용 완료가 아니다.
공용 webserver를 먼저 배포하고 소비자 Admin을 갱신한다. Daemon OFF를 유지한다.


## 2026-10-18 — 주소 갱신 날짜·예약 복구 후속

- 브라우저 UTC/미국 서부 시간대 PC·모바일 4건에서 날짜 표시 오류를 재현하고 Asia/Seoul 표시로 고정했다.
- 앱별 빌드·타입 검사·단위 211건 및 로컬 실제 API/native Dagster 연동 UI 17건 통과. 이 결과는 시간대 수정까지의 결과이며 운영 배포 증거가 아니다.
- 적대 리뷰에서 발견한 응답 유실 후 늦은 예약 중복 문제를 Common 영속 요청 ID mutation으로 연결 중이다. NOT_STARTED 실행은 재조회 완료로 처리하지 않고 제출 복구하며, 새 mutation 미지원 서버에서 기존 무작위 launch로 우회하지 않는다.
- 신규 예약 복구의 실제 SDK·재시작·지연 커밋 검증, 두 독립 리뷰, CI 및 운영 배포는 미완료다. daemon OFF를 유지한다.


## 2026-10-18 주소 확인 집계 후속 종결

Common40·Weather113은 두 독립 FULL SOURCE PASS/CI 후 머지했다. 제품2bd779의 전체661·CI661,
새 pin의 loopback UI13과 원천 보존을 [최종 원문·수용 범위](reviews/2026-10-18-address-summary-final.md)에
보존했다. 운영 migration/provider/배포·수집 CPU/RSS와 Transport 별도 gate는 후속이다.
아래 구현 당시 대기 문장은 당시 이력이며 Daemon OFF를 유지한다.

## 2026-10-18 — 비활성·이동 지점의 주소 확인 집계 보정

Transport86 r2에서 찾은 공통 MAX 집계 문제는 Weather 비활성 위치와 좌표 이동에도 해당한다.
실제 WeatherLocationRow를 갱신하는 3개 반례를 이전 Common c067에서 3 FAIL로 확인하고,
Common40의 522525a pin과 잠금을 갱신한 뒤 주소 회귀 4 PASS를 확인했다.
파생 과거 행을 보존하고 source 지역·KMA 격자·관리자 metadata 불변도 검증한다.
전체 CI·두 독립 리뷰·UI 및 운영 수용은 각각 별도다. 앞선111/112 이력을 덮지 않는다.

## 2026-10-18 주소 갱신 구현·로컬 검증

VWorld 우선·Kakao fallback, 위치별 달력 6개월 자동 갱신과 Admin 수동 실행을 구현했다. 두 앱은 같은 Common Python 엔진을 고정 commit으로 사용한다. [설계·설정 및 검증 범위](reverse-address-refresh.md)를 참조한다. 원천 주소·좌표는 보존한다.

로컬 실브라우저 최종 재검증은 앱별 13개(합계 26개) 통과했고 실제 인증·상태 API·Dagster 큐 예약·중복 차단·모바일/키보드·오류 복구를 포함한다. 제공자는 주입 fixture다. Python 전체 검증은 Common 509개, Weather 658개, Transport 838개 통과·환경 조건 10개 skip이다. Common39·Weather111은 두 독립 리뷰/CI 통과 후 머지했다. [최종 리뷰 기록](reviews/2026-10-18-address-refresh-final.md)을 참조한다. Transport 정식 gate는 별도이며 운영 migration·실제 제공자 실호출·배포·운영 UI 수용은 미실행이다. Daemon은 중지 상태를 유지한다.

## 2026-10-17 — 최신109와 남은 운영 수용 문서 정산

- 이 문서 PR 생성 전 main39aafe0/OPEN0, #108/#109 exact head·MERGED·PR/main CI 성공을 공개 GitHub 읽기로 확인했다. 코드/config/tests 변경0.
- root 공개 safe의 CS71ff/139f 배포·API8d/d48c 유지, UI36PASS, native run8d87945 complete_snapshot/영속8필드1Observation, daemonOFF·queue3 보존을 [감사](audits/2026-10-17-remaining-work.md)에 구분했다.
- 11:58 RO의 health90/90·77/16, Weather checkout probe 부재와 대체 source 경로/전체 module 미확인 한계를 기록했다. source 소실이나 미배포로 단정하지 않았다.
- [tasks](tasks.md)/[resume](resume.md)를 새 현행 인덱스로 추가했다. 기존 deploy/n150의 계획/실패/체크와 vendored provider 원장은 보존했다.
- 지역 freshness·운영복구·장시간 CPU/RSS·consumer/정식 build 경로 수용은 잔여다. Manager source/runtime probe drift는 공통 소유 후속이며 Weather에 Geo token 소비를 추가하지 않는다.
- 사용자 승인 문서 commit [skip ci]. 링크·diff·사실 대조만 수행, full suite/이미지 build/새 live UI/서비스 제어0. 독립2리뷰/PR merge는 root가 맡는다.


## 2026-10-18 수집 카탈로그 CPU/할당 peak 개선

metadata 포함 카탈로그를 ORM 대신 DTO 컬럼 projection으로 조회한다. 실제 DB→DTO→target
1,450행 로컬 비교와 한계를 [측정 기록](collector-catalog-cpu-20261018.md)에 기록했다.
두 명의 독립 소스 리뷰·exact-head CI·ARM64 API/CS native build·r4 prepare는 통과했다.
ARM 격리 fixture 10표본에서 CPU 중앙값 약 12.2%, Python 할당 peak 약 16.0% 감소했고
RSS의 실질적 저감은 주장하지 않는다. API/CS 배포065661ca PASS와 배포 후 WSL
Chromium1243 UI36 PASS/exit0를 확인했다. 이 수용 기록은 PR #117에 포함한다.
실제 provider 전체 수집 CPU/RSS·장시간 안정성·Kakao 실제 fallback/주소 전체 sweep 및
Track 1/2의 미검증 수용은 별도로 남긴다. 이전 pending 문장은 각 기록 시점의 이력이다.
Dagster daemon OFF를 유지한다.


## 2026-10-18 PR #118 비특보 예보 배치 메모리 개선 — 배포·UI 수용

제품 a8949778은 다음 위치가 상한을 넘기기 전에 기존 묶음을 게시하고,
격자·중기 예보의 staging·게시 상한을2500으로 분리했다. 특보 staging과
다른 provider의 공용 게시 상한은5000을 유지하며 한 위치는 나누지 않는다.
격리 PostgreSQL 전체667개·Ruff·최종 Python/frontend CI·독립 소스 리뷰2건이 통과했다.

Odroid의 고정 합성 입력(792fact×10위치)에서 기준/후보 각3회 비교를 두 측정군으로
수행했다. PostgreSQL을 포함한 측정군은 worker RSS 중앙값 약14.99% 감소,
합산 CPU 약1.29% 감소, 처리 시간 약2.02% 증가였다. CPU는 작은 표본에서 동등
수준으로 판단한다. SQL은107→125이며 전체 provider·전체 job·호스트의 개선율은 아니다.
실측은6421a4b에 상수2500을 주입한 결과이고, 실제 a8949778 기본값은 별도로
rollback+alias 및 commit 후 응답 오류의 복구2건을 통과했다.
각각2376+5544,4752+3168건으로 총7920건을 복구하고 정상 기준의 원천·fact·current
의미 digest 일치와 다음 재수집0건을 확인했다.

[상세 측정·원시 증거·복구 및 한계](reviews/collector-bounded-stage-20261018.md)를 따른다.
이번 제품의 ARM64 API/CS native 이미지 빌드와 각 이미지의 설치 소스67파일 검증이
통과했다. 운영 전환 f5cc3880에서 API/CS READY·기존 큐와 env 파일 보존·버전 외 runtime
환경 동일·daemon OFF를 확인했다. 배포 후 WSL Chromium1243 live UI는36/36 PASS,
exit0이며 Weather 세 역할의 실행 상태가 유지됐다.
[배포·UI 수용 증거](reviews/collector-bounded-stage-deployment-20261018.json)를 따른다.
PR #118은 아직 머지 전이며 daemon OFF를 유지한다. 전체 수집·24시간 OOM/swap·자동 복구·Track1/2의 미검증
운영 수용을 완료로 바꾸지 않는다. PR #117의 RSS 미입증 문장은 당시
카탈로그 조회 범위의 기록이며 이번 격자 저장 실측과 구분한다.

## 2026-10-19 지역 관측 제외 사유 보강

W-OPS-01 최근 native 실행·run-source 연결을 읽기 전용으로 대조했다. 유효값 없는 지점의 skipped_without_values를 추가해 좌표 부재와 구분한다. 격리 PostgreSQL 전체 668개·frontend 214개·type-check·Ruff 및 CI39170492571이 통과했다. 두 독립 코드 리뷰의 열린 지적은 0개다. 공개 좌표의 실제 VWorld 호출과 오류 주입 후 실제 Kakao fallback도 통과했다. 운영 API/CS 전환5c8db5f8과 배포 후 UI36이 통과했으며 daemon OFF를 유지한다.

현재 상태 정정: 앞선 #118의 머지 전 표시는 당시 이력이다. #118은 4757df0c0b5090b22142535c623b77abc657f382로 머지됐고 a8949778 제품 배포와 UI36을 통과했다.


## 2026-10-19 지역 수집 수동 수용 초안

#119 이후 제품683e446d를 그대로 사용한 두 asset 제한 수집의 기록을 분리했다. metrics 사전 probe 실패와 실제 DB6403건 저장 후 Dagster 결과 경로 실패를 구분해 보존하고, 소유 metrics·NVMe state 경로와 시간 역전/정상 noop 감사 반례를 보강했다. 세 번째 실행 결과는 아직 정산 전이며 daemon OFF를 유지한다.


### 지역 수동 수용 최종 정산

앞선 초안 이후 r3 두 asset native SUCCESS·DB complete를 확인했다. 연결6403건 중
휴게소1147건은 정상 재수집으로 신규0, 산악5256건은 신규 저장이었다.
좌표59·빈값16 제외를 직접 기록하고 중복·빈 raw0을 대조했다. worker CPU66.304초와
절대 cgroup peak261.15MiB는 단일 수동 실행값이며 성능 개선율을 주장하지 않는다.
수집 후 UI36도 PASS, 제품683e446d·daemon OFF·큐3개는 유지됐다.
실패 r1/r2와 기존 DB 저장은 보존했다. 전체 주소 수동 갱신 및 daemon 자동 운영·
장시간 전체 수집은 별도 수용이며 추가 성능 튜닝보다 남은 Track 수용을 우선한다.


## 2026-10-19 전체 주소 수동 cycle 수용 완료

전체 1,450개를 30개 native SUCCESS 배치로 갱신했다. DB 감사에서 원천 SHA 불변,
유효 주소 1,450개, due/pending/failed 0, cycle owner 해제를 확인했다. desktop/mobile
완료 UI 6개 검사도 통과했다. 첫 50개 판정기의 INFO 로그 부재는 기록에 남기고,
이미 저장된 행은 재호출하지 않았다. 상세 결과와 daemon 재가동 뒤의 별도 queued/started
관측은 [전체 주소 수동 갱신 수용](audits/2026-10-19-address-full-cycle.md)에 기록했다.
예약 작업과 장기 자동복구는 이번 수용 범위가 아니다.
# 2026-10-23 Map KMA 예보 배열 API

- W3 초단기/단기 예보를 승인 generation vector와 위치별 chunk index로 제공한다.
- 원본 known_at과 publication 시각을 분리하고 next_since 델타를 도입했다.
- 늦은 commit, 동일 clock, 같은 bytes 재승격, 빈 델타, cursor/주소 fallback 격리를
  실제 PostgreSQL API 회귀로 확인한다. 운영 설정은 기본 OFF 그대로다.
- 상세 계약: `docs/reports/2026-10-23-frozen-forecast-api.md`.

## 2026-10-23 W4 수집 상태 projection 기반

- provider/dataset/style별 작은 상태표와 terminal·일정 시각 fence를 추가했다.
- 옛 worker/중복 terminal이 최신 상태를 덮지 않으며 실패 때 이전 성공을 보존한다.
- PostgreSQL 14건에서 실제 동시 잠금 대기 뒤 옛 실패 거절/rollback/KST/일정 중지와
  PK 단일 SELECT를 확인했다. 전체 CI·두 리뷰·실제 REST 연결·운영 수용은 별도다.
- 모듈만으로 status API가 활성화되지는 않는다. native CAS/savepoint/일정 writer와
  API pagination/ETag는 API 소유자의 통합 작업이다. W4 완료로 세지 않는다.
- 상세 계약: `docs/reports/2026-10-23-export-source-status.md`.

### W4 실제 fetch-clock와 metadata 잠금 격리 보강

- canonical 별칭의 최초 fetched_at을 재수집 시각으로 오인하지 않도록 작은 run/dataset
  실제 clock을 추가했다. clock 미확인은 null이며 finished_at으로 대신하지 않는다.
- 상태 작업의 savepoint/statement 5초·lock 1초 상한과 더 짧은 기존 GUC 유지·복원을
  추가했다. PostgreSQL 집중17건에서 실제 잠금 실패 후 native SQL 사용을 확인했다.
- 원본 ingest/terminal·REST/일정 hook 통합과 두 리뷰·CI/운영 수용은 아직 별도다.

# 2026-10-23 W5 관측 일간 요약

- 승인 generation 기반 시간 winner reducer와 사전계산 일간 세 구간 REST를 추가했다.
- KMA NOWCAST 포함, provider/replay 중복 제거, 강수 누적/기간 불명 제외, 단위 변환,
  KST 구간 경계·partial·무관측 null 정책을 문서화했다.
- 원본 fact/이력/기본 OFF를 유지하고 publisher 명시 opt-in만 제공한다.
- 상세: `docs/reports/2026-10-23-daily-summaries.md`.

# 2026-10-23 W1 독립 reference와 외부 현재·예보 export

- Open-Meteo/WeatherAPI/OpenWeatherMap은 기존 정규화를 원천 raw에서 네트워크 없이
  독립 재실행하고 요청 좌표/전체 시간·배열/필수 metric/급감을 검증한다.
- HTTP 전 실제 quota/sampling cohort를 보존하고 native 성공·소유권·파일 증명 뒤
  current/external forecast를 제공한다. KMA style은 해당 KMA scope만 선택한다.
- W1 reference는 날씨 head와 분리했다. 실제 catalog fetch clock은 아직 없으므로
  source_collected_at=null/stale로 표시하고 updated_at을 원천 시각으로 쓰지 않는다.
- 기본 OFF/원본 fact·이력은 유지한다. 전체 provider/mid, 두 리뷰/CI/운영 비용·GC
  gate는 진행 중이다. 상세: `docs/reports/2026-10-23-external-exports.md`.

### 최신 cohort의 native 실패와 승인 확인

- 독립 리뷰에서 발견한 claim 이전 prepare 실패 경계를 보완했다. 최신 cohort의
  native 성공과 각 head의 승인 run 일치를 동일 reader snapshot에서 확인한다.
- 새 attempt/owner가 생기지 않은 HTTP 실패도 current/forecast의 이전 집합과 cursor를
  stale로 유지하고 이전 healthy ETag304를 막는 실제 PostgreSQL/API 회귀를 추가했다.
- cohort 쓰기 자체 실패도 native 시작 INSERT의 원자적 opt-in fence와 부분 index의
  최신 한 행으로 확인한다. 기존 opt-in하지 않은 실행은 무시하며 이력 전체를 읽지 않는다.
- export 활성화가 기존 중기예보/특보 asset에 미지원 인자를 전달하지 않도록 제한하고
  실제 asset→기존 수집의 두 제공처 경로 호출을 회귀 검증했다.

### 공개 SDK 원문 재해석과 페이지 완결 검증

- AirKorea·산림청 산악/청정넷·KREX·KHOA 공개 parser와 기존 Weather 정규화를 재사용해
  관측소/시각/수치/source key를 검증하고 기존 native capture·성공 head에 연결했다.
- AirKorea 실제 vendor 소스와 KHOA/KREX 최종 SDK pin을 함께 갱신했다. 원본 라이선스와
  provenance를 보존했다. KHOA 원문 좌표의 NaN/무한대/범위 밖/bool도 거절한다.
- SDK typed DTO만으로 전국 완결을 주장하지 않는다. 작은 페이지 receipt와 KHOA 장소별
  원문 조합을 추가하고 누락·개수 변동·중복 구간을 거부한다.
- 실제 Dagster source 배선·누적 inventory·전체 provider/mid REST·GC/운영 비용 gate는
  후속이다. 운영 flag OFF와 기존 수집/API를 유지한다.
- 상세 계약/검증: `docs/reports/2026-10-23-sdk-source-exports.md`.

### 실제 source inventory와 quota 회전 준비

- provider/dataset/location의 마지막 승인 metadata를 head와 같은 TX에서 upsert하고
  quota 선택 밖 이전 위치는 유지한다. source raw/fact는 복사하지 않는다.
- 작은 namespace 키셋으로 이전 승인 head를 재투영하는 bootstrap helper와
  native intent가 있는 source의 HTTP 전 명시 roster 선언을 추가했다.
- inventory generation FK/live-root 계약을 명시했다. 파일 GC는 아직 없으며
  reader·실제 수집 배선·전체 provider/mid·운영 비용 검증은 후속이다.
- 상세 설계/task: `docs/reports/2026-10-23-export-source-inventory.md`.

### AirKorea 승인 source 실제 수집 연결

- 명시 export opt-in일 때만 native 시작 intent와 HTTP 전 canonical 측정소 roster를
  고정하고, 기존 public SDK 정규화 결과를 원본 fact/lineage와 함께 캡처한다.
- 원천 실패 다음에도 이전 승인 값과 cursor는 남으며 source 상태를 stale로 유지한다.
  기존 OFF 경로와 기존 AirKorea의 요청 실패 집계 동작은 유지한다.
- quota 회전 current reader, 기존 AirKorea/asset와 0033 실제 upgrade를 포함한
  PostgreSQL 집중 43개 및 전체 Ruff를 확인했다. 전체 SDK 배선·REST 기본 선택과
  운영 비용/GC gate는 아직 후속이며 운영 설정은 변경하지 않았다.


# 2026-10-23 중기예보 export 재해석 증거

- 공개 SDK의 중기 DTO와 기존 Weather normalizer로 저장 원문을 독립 복원하는
  순수 모듈을 추가했다. 발표/가용 시간은 SDK 공개 API를 사용한다.
- land/temperature 지역·회차·lineage·전체 필수 구간·숫자를 검증하고 불완전 증거는
  거절한다. 집중54건/Ruff PASS이며 실제 수집·head·REST 배선과 운영 적용은 후속이다.
- 상세 계약과 남은 배선 조건: `docs/reports/2026-10-23-mid-export-proof.md`.
### SKY 원문 타입 검증

- 두 독립 초기 리뷰가 지적한 기존 normalizer의 `str()` 변환 경계를 수정했다.
  normalizer를 바꾸지 않고 보존된 원문 `wf`가 실제 서술 문자열인지 검증한다.
  bool/숫자/객체/배열·NaN/Inf·숫자/sentinel을 camel/snake alias 모두에서 거절한다.
- 올바른 source hash를 재계산한 추가32건 포함 집중86건/Ruff PASS. 최초54건과
  합산하지 않으며 최종 두 리뷰·정확 CI는 별도 수용한다.
### 중기 지역의 스트리밍 fan-out

- 한 육상 지역이50곳을 넘더라도 source scope를 잘라 중복 claim하지 않도록
  descriptor 상한을1,000곳으로 확장했다. 공개 SDK/기존 normalizer로 만든26/16개
  템플릿만 보관하고 위치별 iterator로 내보낸다. 큰 전체 목록 복원은 거절한다.
- 기존86건과 스트리밍4건을 함께90PASS/Ruff PASS.1,000곳 순회의 tracemalloc peak
  2MiB 미만을 확인했으며 운영 RSS/CPU 개선이나 실제 배선 완료로 세지 않는다.

### SDK 관측망 실제 수집 연결

- 산악·청정넷·KREX·KHOA의 native 시작에 명시 export intent를 함께 저장하고
  기존 canonical anchor의 작은 필드만 읽어 HTTP 전에 source 계획을 고정한다.
- KREX 부분 파싱/개수 불일치는 승인하지 않는다. KHOA는 공개 Page의 원문 수를
  검증해 페이지에 나뉜 장소 예보를 조합하며, 불완전하면 기존 native 경로만 보존한다.
- 반환되지 않은 source 계획은 실패로 마감해 영구 preparing 재시도를 피한다.
  청정넷 과거 관측은 기존 native 이력에 모두 남기고 최신 한 건만 current에 승인한다.
- 기존 regional/OFF와 SDK page 회귀를 포함한 집중62건 뒤 보완한 실제4종 native
  runner/부분 KREX 회귀4건을 확인했다. 운영 CPU/메모리 비용은 별도 실측 gate다.

### inventory 적대 리뷰의 복구 경계 보완

- 승인 위치가 활성 roster에 없을 때 같은 RR에서 명시 비활성화만 제외한다.
  실제 catalog 유실은 이전 완전 vector와 cursor를 stale로 보존한다. disable→reenable
  후 과거의 더 작은 snapshot이 fallback으로 선택되지 않도록 최신 handle만 재사용한다.
- 0033 upgrade/downgrade에 statement60초/lock5초 상한을 두고 더 짧은 caller 설정을
  보존·복원한다. 실패 시 Alembic TX가 DDL/설정을 함께 rollback한다.
- 선행 최신 관측 선택의 잘못된 anchor는 export만 무효화하고 원래 native 순회의
  실패 시점과 이미 저장한 chunk를 보존한다. 시험의 export metadata도 매번 격리한다.

### 전체 현재 source와 SDK 외부 예보 REST

- current 기본 query가 KMA 실황·AirKorea·산림청2종·KREX·HTTP3종의 승인 합집합을
  반환하고 KHOA를 external forecast에 제공한다. 초기 필수 누락503/실패stale 계약을 유지한다.
- KMA base의 native intent와 HTTP 전 실제 격자 cohort를 연결했다. SDK 새 station은
  catalog bootstrap 뒤 다음 사전 계획에서 승인하며 빈 응답도 한 번 재확인한다.
- 중기 전용 grouped reader가 안전한 공통 pagination/TTL/ETag를 재사용하도록
  STYLE_DATASETS/manifest/page 출력의 좁은 protected hook을 분리했다.
- 실제 7 provider current/KHOA REST·SDK43PASS, 재확인/새 station 복구/registry10PASS.
  전체 제공처 일간 요약·최종 리뷰/CI/실제TCP/UI와 운영 비용/GC gate는 남아 있다.

### 일간 요약의 다중 원천 승인 coverage

- 실제 현재 dataset registry와 위치별 승인 generation에 맞춰 reducer 진도를
  확인한다. 최초 미계산은503, 이전 요약만 있으면 stale로 유지한다.
- 관측 dataset 대상이 아닌 예보 전용 위치는 null/sample_count=0과 unknown
  원천 시각을 제공하고, 산림청/KREX 섭씨 `℃`를 지원한다.
- 이력 전체 조회·새 이력 표·운영 수집 실행 없이 작은 summary/coverage만 사용한다.
  집중 시험·두 리뷰·정확CI/실제HTTP/UI 및 운영 비용/GC gate는 수용 전이다.
# 2026-10-23 export artifact의 제한된 동시 읽기

- current/forecast/일간요약의 파일 읽기에 공유 guard를 적용하고 writer 배타 guard는
  유지한다. 같은 root의 OS 파일 slot으로 전체 프로세스를 합쳐동시2개까지만 허용한다.
- 별도OS process의실제잠금/용량/강제종료회복3건·Ruff PASS. metadata→파일 사이
  in-flight pin과GC/운영CPU·RSS 수용은별도이며운영flag/daemon은바꾸지않는다.
- 상세: `docs/reports/2026-10-23-export-shared-read-guard.md`.
### writer 기아 방지

- 초기 독립 적대 리뷰의 연속 SH reader에 의한 writer timeout 경계를 admission
  turnstile로 보완했다. writer 대기 중 새 reader를 막고 기존 reader만 drain한다.
- 단일 deadline/두 slot/기본 EX 보존 및 실제OS writer진입회귀 포함4PASS/Ruff.
  테스트의 강제종료 뒤 Event 정리 교착은 시험 harness에서 수정했고 실패실행은
  통과로세지않았다. 두 최종 리뷰/CI·기존PG·HTTP/UI 수용은 별도다.
# 2026-10-23 중기 전용 job의 export 배선

- 명시 opt-in 중기 job에서 실제 source roster/사전 계획과 capture를 연결한다.
  동일 지역을 중복 HTTP/claim하지 않고 SDK 회차를 한번 고정한다. 한 원문·작은
  template과50곳/2,500값 batch로 원본 저장·전체 예산·heartbeat를 유지한다.
- 공개SDK 모의HTTP+실제native runner6건/기존OFF23건/Ruff PASS. PostgreSQL
  통합·두 리뷰·정확CI/HTTP/UI·운영 성능은 별도 수용 전이다.
- 상세: `docs/reports/2026-10-23-mid-export-collection.md`.



### 중기 실제 PostgreSQL 저장·복구 수용

- 전용 DB에0001→0033을 새로 적용한 뒤 실제 SDK 모의HTTP/native runner/capture/
  publisher에서3개 head·6개 역할 승인과 후속 HTTP 실패 시 이전6개 승인 보존을
  확인했다. 관련PG10건14.01초·실제asset ON/OFF21건·Ruff PASS.
- 초기 검증 두 실패는 공개SDK 예외 문구와 publisher REJECTED 상태의 기대값 오류였고
  테스트만 수정했다. source 자체 변경은 없다. 두 리뷰·정확 CI/REST/UI는 후속이다.
### 중기 빈 응답 재확인

- root 자체 점검에서 빈 성공 응답 재확인 요건이 빠진 점을 찾아 같은 회차·계획으로
  1회만 재확인하도록 보강했다. transport 재시도는 SDK 경계를 유지한다.
- 집중8건/Ruff PASS. 원천이 계속 비면 native 실패/이전 승인 유지 경로를 사용한다.
### export와 native 선택 상한 분리

- root 자체 점검에서 descriptor1,000곳 상한이 기존 native 입력까지 막을 수 있는
  경계를 찾아 작은 native scope와 export descriptor를 분리했다. 승인 불가면
  원본 bounded 저장만 계속한다.1,001곳42,042값 포함 집중9건/Ruff PASS.
- 빈 응답 재확인 추가 후 관련PG12건25.38초 PASS도 확인했다. 새 범위 분리와 최종
  선행155수정 통합 후 실제PG를 다시 확인하고 정확 CI/리뷰를 수용한다.
