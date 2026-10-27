## 2026-10-28 — API210 교체 완료·실제 마커 실패 후 요청 예산 후속

- 현재 운영은 R4 API210/image1b3d(actual0,2026-10-27 19:49:40UTC), CS e393/web5098이다.
  새 cold60×3은19701ms200/30020ms504/21016ms200으로 **FAIL**이며 모바일/overlay는 미실행,
  로그아웃 뒤 쿠키 잔여0이다. 아래 API e393/미배포 표현은 당시 이력으로 보존한다.
- 후속7파일은 browser 동시2·같은범위 in-flight 공유·실패수동retry·상세열림시조회와 특보 고정
  predicate literal 보완이다. Backend 집중56/56, frontend265/265·WSL build actual0이지만
  제품이 같은1d2 WSL전체1518 PASS와 시험 한 파일만 보완한a707 Docker전체1517 PASS/1 SKIP를 확인했다.
  PR187의 정확 CI·리뷰 정본과 최종 배포·운영 성능/UI 수용은 따로 판단한다.
- export 목표중지/publisher STOP/Map·PinVi flags OFF를 유지한다. 새 Dagster 이미지 승인은 있으나
  e393→5098의 Dagster 제품 입력이 같고 기존 개선이 운영 중이므로 중복 CS 교체는 하지 않는다.
- 전체 QA 원본27실패는 실행기의 spawn 진입/PYCACHE 환경 문제였고 같은27건 재검증을 통과했다.
  첫 Docker1515 PASS/2 FAIL/1 SKIP는06:01 KST의 미공개 중기회차 fixture 오류였으며 SDK 공개지연을
  반영한 시험만 보완했다. 원문은 보존하고 제품644파일 동일성·고정06:01/18:01 회귀를 구분한다.
- 다음 한 작업: PR187 코드 머지 대상을 고정한 뒤 API+web 후보의 별도 배포·실제25초/모바일/overlay를
  수용한다.21:33 UTC 정산 시점 R6는 준비 상태이고 실제 입력은False/None·새 배포0이다.
  소유 QA의 모든 실행·DB가 끝났고 PG는 정상 exit0으로 종료·삭제했다. Transport G/R과 직렬 조율한다.
  [범위·증거·한계](reports/2026-10-28-marker-request-budget.md).

## 2026-10-28 — 특보 계획 통계 차이 보강

- 1bafa의 첫 CI 실패를 보존하고,23시 변조와 SQL fake는 시험만 보완했다. 통계가 있는 EXISTS 전역 hash는
  ownPG에서 재현하고 OFFSET0으로 상관 계획을 보존한다. 관측/예보/특보 의미와 예산·DTO·writer는 그대로다.
- 관련 WSL119PASS 뒤 최종source Docker/CI/독립 두 리뷰를 진행한다. 새whole·운영요청/배포0이며
  이전actual504/쿠키미계측/25초목표미수용은 남는다. [증거·근거](reports/2026-10-28-marker-alert-validity-query.md).

## 2026-10-28 — 지도 특보 반복 검산 최소화 후보

- 실제6종류 경로의 특보 유효기간 검산을 후보 EXISTS로 합치고 같은 요청 위치 ANY로 범위를 고정했다.
  후보/정렬/top26/reducer·512 cursor·복합 fact 키를 유지한다. 운영 변경0/25초 목표 미검증이다.
- 원6312 RED의 SELECT14/옛 검산8과 요청 밖80행 검산 반례를 보존했다.
  다음은 최종 집중·정확 CI·독립 두 리뷰다. [범위와 증거](reports/2026-10-28-marker-alert-validity-query.md).
  아래 PR184 whole 미실행 표현은 당시 이력이며 원72 두 전체는 actual0으로 종료했다.

## 2026-10-27 — TCP 회귀의 준비 실패 정리 보완

- 현재: 정확72 CI1505 PASS/frontend 성공. 독립 리뷰의 TCP 시험 준비 실패 P2를
  별도72 기반 작업 트리에서 보완해 mock5·정상 TCP 포함6 집중을 통과했다.
- 다음 한 작업: 원72 전체 WSL/Docker의 실제 종료를 회수하고 제품 app/deps/fixture
  동일 증명과 새 cleanup HEAD의 집중·CI·증분 리뷰를 합쳐 판단한다. 원전체 환경은
  고정했고 운영/수집 변경0, 운영25초 목표는 미검증이다.
  [정산](reports/2026-10-27-marker-disconnect-admission.md).

## 2026-10-27 — 지도 취소 PR의 CI fixture 적재 보완

- 현재:962 CI1504 PASS/1 FAIL은 조회 전 projection9,600행의 FK 검사30초 초과였다.
  동일57,600행·FK·assert·제한을 유지하고1지점/960행씩 적재하도록 최소 보완했다.
  로컬 원본/보완 모두 통과했으므로 CI 원인의 완전 재현·운영 속도 개선은 미입증이다.
- 다음 한 작업: 새 정확 HEAD의 CI, 전체 WSL/Docker, 독립 두 리뷰 순으로 검증한다.
  전용 disk-backed2GiB/1CPU·PG100과 별도 두 DB 준비까지 완료, whole 미실행이다.
  [정산](reports/2026-10-27-marker-disconnect-admission.md). 운영/provider/export 변경0.

## 2026-10-27 — 실제 지도6종류·취소 대기 경로 정정

- 기본 지도는6종류를 명시한다. PR180의무필터 경로와 실제 UI 경로를 혼동한 분석을
  정정했다. 기존 EXPLAIN·1443/1442검증은 무필터 범위로 보존한다.
- 실제 ASGI disconnect가 BaseHTTP middleware를 지나200/DB조회로 진행하는 RED를
  재현했다. 순수 ASGI로바꾼 admission7회귀는 PASS이며, 이미 실행 중인 DB thread
  취소·운영25초 목표는 미검증이다. repository/index/DTO/병렬도/timeout 변경0이다.
- 다음은 전체WSL·Docker·정확CI·독립2리뷰 후 새운영 수용을 별도 조율한다.
  [재현·정정·경계](reports/2026-10-27-marker-disconnect-admission.md).

## 2026-10-27 — 현재 Dagster 재배포 불필요 확인·export 취소

- 실제 API/collector는e393(PR176), web은main5098(PR177)이다. e393→5098의Python/Dagster/build/migration 입력은 같아 #174 CommonAA·retention 개선이 이미 운영 중임을 확인했고 중복 build/교체하지 않았다.
- API/CS CommonAA19 source와Weather핵심6hash, actualschema0034/정본API12201 health/version200, 자연schedule retention SUCCESS156.153초를 새 RO로 확인했다. 7runtime·daemon·NVMe 원상태를 보존했으며 CPU/RSS 효과는 미측정이다.
- 사용자 취소에 따라 `weather_export_publisher` sensor1만 STOPPED로 변경·readback했다. 이미 시작된tick111545는 자연SKIPPED로 끝났다. 다른47-native 집합의 나머지 상태/flags/API/data는 변경0이다. 앞으로 이 sensor는 original-RUNNING 복원 예외인 **desired STOPPED**다.
- 현재source 공개UI21은 두 source 리뷰·단독browser슬롯 뒤21/21 PASS18.705초로 수용했다(목록1450/tile20/API2005, 로그아웃·쿠키삭제·로그인복귀/C7없음·7runtime불변). ERR_ABORTED11은 별도다.
- 다음 자연retention도SUCCESS154.611초·resumed true/facts41000을 확인했다. 뒤 materialization의 약4.65일/stall1은 **삭제 위치 기반 lag 상한 지표이며 실제 oldest age가 아니다**. 정시 valid_at bucket 안에서는 진척 중에도 상한이 오를 수 있어 두 표본으로 정체를 확정하지 않는다. 남은행이 있어 과거6h 물리 정리 완료는 미수용이다. [현재 source·취소·UI와 한계](reports/2026-10-27-current-dagster-and-export-cancellation.md). 아래는 각 작성 시점의 이력이며 예전 미배포/새publisher재개 표현을 현재desired state로 읽지 않는다.

## 2026-10-27 — 소유자 결정 기록(export 일시중지·current 분리·GC 전환)

- 현재: export 목표는 소유자 지시로 **일시중지**다. Map `weather_state_*`(locations 제외)·PinVi weather
  flag는 꺼 둔 채로 두고 KHOA 해수욕장 좌표 수정도 보류한다. [결정](decisions.md).
- 다음 한 작업: 2026-11-03 무렵 W-EXP-GC-01 — `ktw_export_artifact_gc_reclaimable_bytes`와 publisher
  `stages=…gc:` 시간을 보고 GC를 `delete`로 바꿀지 정한다. W-EXP-SPLIT-01(current/forecast job 분리)은
  export 재개 때 한다. odroid `weather-current` 표지 [제안](reports/2026-10-27-odroid-weather-current-release-pointer.md)은
  Codex 합의를 기다린다. 마커 지연 등 아래 기존 항목은 그대로다.

## 2026-10-27 — 공용 UI dev.9 채택 PR

- 현재: `@kor-travel/ui` `0.1.0-dev.8` → `0.1.0-dev.9`(Common `aa232ad`, 로그인 hydration 준비 경계) vendoring을
  별도 PR로 진행한다. 산출물 SHA256 `b738d882…b403`, 앱 코드 변경 없음. CI·독립 리뷰·머지 상태는 PR이 정본이다.
- 다음 한 작업: 머지·배포 뒤 공개 UI 로그인(SSR 직후 비활성 → hydration 뒤 활성, 정상 로그인/오류/재시도)과
  브라우저 저장 계정 자동완성 동작을 확인한다.

## 2026-10-27 — Common aa232ad·단기예보 정리 수정 PR #174

## 2026-10-27 — Weather173 운영 교체 완료·마커 지연 후속 진행

- 배포 merge a9f54의 세 이미지/0034를06:35UTC에 반영했다. 원래21 RUNNING/2 STOPPED·공용 daemon·타 서비스 보존,
  기존 네 인덱스 OID 보존·자연 publisher와 새 정기 current 수집 재개를 확인했다.
- 이 정산 기준 소스 main은1766889다. PR #174/#175는 머지됐지만 이번 운영 a9 이미지에는
  포함되지 않았다. 단기예보 정리 수정·export 후속의 배포/운영 수용은 별도로 남는다.
- 실제 Chromium PC1440/375의 지도 면적·상세 오버레이·실제 가평14.4 값/시각은 통과했다.
  **마커 latency는 미완**이다. 후속60×3의19.506/29.884/28.324초 중 두 개가25초 상한을 넘었다.
  초기70초 수용기 실패·명시 ERR_ABORTED6개를 원본 보존하며 빠른 응답 성공으로 대체하지 않는다.
- [배포·긴 중지 구간·UI 실패와 후속](reports/2026-10-27-weather173-deployment-and-latency.md).
  다음 한 작업은 저장된 실제60IDs/시각과 query/predicate/index/조인/정렬의 병목을 좁히는 것이다.
  취소 safepoint·기존 인덱스 baseline rollback·pause 전 장기 run 대기는 별도 운영 P1이다.

아래 절은 각 작성 시점의 이력이다. 운영 버전·실제 수용의 정본은 위 최신 정산이며,
아래의95020/미교체/머지 전 진행 표현을 현재 운영 상태로 해석하지 않는다.

## 2026-10-27 — Common aa232ad·단기예보 정리 수정 PR #174 (당시 기록)

- 당시 상태: [PR #174](https://github.com/digitie/kor-travel-weather/pull/174)는 Python만 다룬다. Common
  `aa232ad` 고정과 `weather_short_forecast_retention_job` 상시 실패 수정(검사한 위치 이어 읽기·ctid 삭제·
  실행당 예산 설정 기본150초·진척0 또는 뒤처진 채 7회(약70분) 연속 줄지 않을 때만 Failure)이다. n150 CI-parity 게이트와
  GitHub CI·독립 리뷰 결과는 PR이 정본이다.
- 당시 다음 한 작업: 머지·배포 뒤 job 첫 run SUCCESS(`resumed=false`→다음 run `true`)와 `backlog_lag_seconds`
  감소를 확인한다([확인 방법](shortterm-retention.md#배포-후-확인)). 이어서 `@kor-travel/ui` dev.9를 별도
  PR로 vendoring한다(로그인 hydration 준비 전 입력·제출 비활성).

## 2026-10-27 — 지도 마커 실제 값 수용 보강 중 (173 배포 전 이력)

- 현재: 운영95020의 지도 면적·상세 통과와 별도로60ID 지연/504·미확인 구름을 재현했다.
  별도 후보는0034 인덱스4개·제공처 대표 병합·특보512키 스트리밍·실패 재조회를 포함한다.
- 집중PG15/계약27·migration보호31·frontend259/type/lint/build·Ruff 통과. 후속 정확SHA 전체CI·독립2인 리뷰,
  수집/queued/실worker 자연 종료 후 공식 배포와 실제 마커 값 수용이 다음 작업이다.
- 새 이미지 교체 완료가 아니다. daemon/PG/타 프로젝트 유지와 배포직전 instigator 개별
  상태를 보존한다. [실측·남은 수용·되돌리기](reports/2026-10-27-marker-batch-and-unknown.md).

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

## 2026-10-26 — metrics key 검증 메모 PR #171 진행 중

- [PR #171](https://github.com/digitie/kor-travel-weather/pull/171)은 scrape 안의 정상 raw key만4096개/262144문자로 메모한다. 상한 초과는 원 검증 fallback이며 cross-scrape cache·inode/mtime 신뢰는 없다.
- 구현86f4의 새10회귀·Ruff 및 frontend CI PASS. 정확한 Python/최종 head CI와 두 독립 리뷰가 남아 있다. 구 로컬 venv의 Common/KMA import 실패와 PG autouse 연결거부는 제품 실패로 세지 않았다.
- 합성 파일979개/32key의 endpoint CPU·whole bytes 동등성만 비교했으며 운영 ARM·전체 수집·RSS 개선을 주장하지 않는다. [설계와 증거](design/2026-10-26-metrics-scrape-key-validation.md).
- 현재 main merge·운영 적용·서비스/수집 설정 변경은 하지 않았다. 기존 원본 dirty4개와 아래 이력은 보존한다.

## 2026-10-25 — 공용 지도 작업면 높이·접기·확장 로컬 수용

- 현재 상태: `/weather` 전용 bounded shell과 Common dev.8 작업면·필터·상세 슬롯을 연결했다.
  고정 `70dvh`/최소 높이·이중 목록 스크롤을 제거하고 모바일 지도 시작과 남은 높이를 실측했다.
  [변경·후보 증거·미완](reports/2026-10-25-weather-map-workspace.md).
- 독립 리뷰의 타일 회복 후 경고 잔류와 출처503 목록 첫행 가림을 실제 앱·양 브라우저로 재현·수정했다.
  실제 성공·현재 표시 범위를 함께 확인해 지도 오류만 해제하고 목록 경고는 정상 flow에 둔다.
- 최종 로컬 검증: Common `a74ac80`/pack `71d9f6…`에서 타입/lint(기존 경고1)·Vitest253·package build·
  두 브라우저 전체 UI34개와 타일 주입10개 PASS다. 기존32개에 출처503 목록 회귀2개를 추가했다.
  모바일 기본 stage 시작233px, PC 아래 여백24px, 글자200% 확장 stage285px·상세 body122px를 확인했다.
  zoom44px 비겹침·원복·중첩 Escape·카메라/양방향 초점·출처 portal의 외부 focus를 보존했다.
- 다음 한 작업: [PR #170](https://github.com/digitie/kor-travel-weather/pull/170)의 후속 source Docker·CI·독립 두 리뷰를 정산한다.
  이전 `3554f7c`의 CI40176053743·Docker253/type·별도 실제 API/타일 PASS를 새 source 통과로 대체하지 않는다.
  API/수집/DB·운영 타일/배포 수용과 W-UX-02/03의 날씨 정보 기능은 별도다.

## 2026-10-25 — 첫 작은 UI 묶음

- 현재 상태: Weather navy/dark 공용 토큰 연결, 지도 제목·상세 메타 `i`, `/weather` 전용 compact 헤더를 구현하고
  PR #169를 main `931502e8`로 머지했다.
  수집 시각 없음·오래됨·특보·오류는 바로 보인다. coarse 포인터의 `i`는 44px이다.
- 로컬 검증: WSL 타입/lint(기존 경고1)·Vitest253개·Chromium/Firefox PC/모바일 UI8개 PASS.
  [범위·증거·미완](reports/2026-10-25-weather-ui-compact.md). 최종 CI·독립 리뷰·머지 상태는 [PR #169](https://github.com/digitie/kor-travel-weather/pull/169)가 정본이다.
- 후속 작업: C-MAP-01 공용 외곽·실제 가용 높이를 Map 회귀와 함께 구현하고 양 소비자가 채택한다.
  이 묶음 당시 모바일 지도 시작 약427~441px는 목표240px 이내를 충족하지 않았다. 후속 실측은 위 최신 절을 따른다.
  실제 운영 타일·기상 자료·배포는 fixture 검증과 별도다.

## 2026-10-25 — 앱·웹 조사와 지도 후속 설계 문서

- 현재 상태: [웹·Android/iOS 조사](research/2026-10-25-weather-ux.md)와
  [후속 설계](design/2026-10-25-weather-map-improvements.md)를 작성했다. 기존 admin 흐름을 유지하고 필요한 API만 보완한다.
  source에 이미 있는 overlay·종류·현재/미래 분리는 보존하며 부모/높이·공용 토큰·정보 우선순위를 개선 대상으로 삼았다.
- 문서 검토 상태: 두 차례 정합 점검과 독립 Popper 1명의 P2 수정 재검토 PASS를 마쳤다.
  [검토 기록](reports/2026-10-25-weather-design-review.md). 최종 머지 상태는
  [Weather #168](https://github.com/digitie/kor-travel-weather/pull/168)·[Transport #130](https://github.com/digitie/kor-travel-transport/pull/130)이 정본이다.
- 다음 구현의 선행 작업: C-MAP-01과 함께 실제 배포 버전·부모/높이·프로젝트 토큰을 대조한다.
  기능 코드·DB·운영·빌드·배포는 이번 문서 작업에서 변경하지 않았다.
- 구현은 C-MAP-01/W-UX-01~03 미완 작업으로 추적한다. 아래 과거 fixture/UI 통과 기록은 현재 운영 지도 치수·데이터 정상의 보증이 아니다.

## 2026-10-24 — 공용 지도 작업면 채택 후보

최신 main106a31e의 보안 정합을 보존했다. 현재 날씨/미래 예보의 마커 시각 혼합을 재현·수정하고
WSL 타입/lint·frontend253·빌드와 Chromium375/1440px2개를 통과했다. 최종 CI·독립 재리뷰는 진행한다.
Python CI1280통과/2실패의 고정 발표/현재 조회 시각 혼합 fixture를 수정했고 격리PG 관련89개/Ruff PASS다.
전체 CI를 다시 확인한다. 운영의18시간 제한과 UI 실행 소스는 변경하지 않았다.
ee82c26 전체CI가 통과했다. 이후 전체6종 선택의 예보 마커 누락과 catalog 대기/실패의 empty 혼동을 수정하고
WSL 타입/lint·253개·빌드와 지연/502/정상empty를 포함한 UI2개를 통과했다. 최종 재리뷰/새head CI를 확인한다.

특보·실황·초단기·단기·중기·관측 종류 필터를 제공한다. 제공처와 종류는 같은 저장 행에서 LIMIT 전에 선택한다. 특보는 observed와 분리한다. 마커는 metric별 대표를 보존하고 미래 예보는 별도 forecast 배열로 반환한다. OpenAPI와 TypeScript client를 함께 갱신했다. KMA 중기 문자열·강수 우선순위를 보강하고 상세는 지도 위에서 닫기/Escape·포커스 반환을 지원한다.

WSL 타입·frontend251·종류/제공처 PostgreSQL20 PASS. Chromium375/1440px 필터30px·176px·PC한줄·실제좌표/줌·종류요청·상세배치·선택위치이동·Escape/포커스·가로 overflow2 PASS. 독립 Popper 지적을 반영했다. 전체 Python·CI·최종 소비자 통합 gate는 아직 정산 전이다. [정산](reports/2026-10-24-shared-map-ui.md). 다음은 후보 CI/최종 소비자 검증이며 자체 운영 빌드·배포는 하지 않는다.

# Weather 현재 인수인계

## 2026-10-24 — 여섯 프로젝트 npm 보안·버전 정합

- 현재 상태: npm 보안·공통 버전 소스 정합 및 WSL 검증 완료. [버전과 예외](npm-security-alignment-2026-10-24.md). 최종 CI·UI·머지 상태의 정본은 [PR](https://github.com/digitie/kor-travel-weather/pull/166)이다.
- 다음 한 작업: 운영 반영을 요청받으면 Manager 정본과 현재 배포 런북에 따라 릴리스를 적용한다. 소스 머지와 운영 배포는 구분한다.

## 2026-10-24 — 현재 상태: Next.js 보안 갱신 후보

- `next`와 사용하는 `eslint-config-next`를 `16.4.3`으로 고정했다. WSL 프론트엔드 테스트 243건·lint·type-check·build PASS. 변경 범위와 남은 취약점은 [작업 기록](journal.md)을 따른다.
- 다음 한 작업: Draft PR의 CI를 확인한다. James/Popper 독립 사전 리뷰는 추가 P0/P1 없이 마쳤다. 운영 배포·실제 서비스 UI 수용은 별도이며 기존 운영 과제는 아래 기록을 보존한다.

## 2026-10-24 — W1~W5 source 구현과 운영 잔여 정렬

- 감사 기준 main9aa0d339(#164)에는 W1 위치·W2 현재·W3 단기/중기/외부 grouped 예보·W4 상태·W5 승인 관측 사전 요약이 구현·머지돼 있다. [현재 인계와 PR](reports/2026-10-23-map-service-api-handoff.md), [남은 운영 과제](tasks.md)를 따른다.
- 기본 service export OFF·별도 publisher 수용, source 정상 bootstrap·요약 초기 투영·공유 NVMe 권한·GC/live-root·ARM 비용과 Map 실제 소비/UI는 완료로 세지 않는다. 운영 #130 배포와 후속 API 전체 적용은 별개다.
- source vector8,192, catalog/page·개별 cohort5,000, inventory20,000 및4MiB 페이지/8MiB chunk·manifest 상한을 구분한다. 기계 OpenAPI/DTO/예시는 현재 source와 대조했고 이번 정산은 문서만 변경한다.
- 아래 2026-10-23 설계·단계별 미완 표시는 당시 기록이며 현재 source 미구현 목록이 아니다. daemon은 최신 사용자 정책 RUNNING을 따른다.


## 2026-10-23 — KMA pagination 선행 보강

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



## 2026-10-23 Map 서비스 export 설계 — proposed, 구현 미착수

[설계 정본](design/map-service-exports.md)과 W-MAP-01~05를 문서로 수납했다.
독립 초안 적대 리뷰 2인은 조건부 PASS다. 2026-10-23 사용자가 기상청 실황 관측 포함을 승인해
W-MAP-03 선택 문서화를 완료했다. 기존 OBSERVED + `python-kma-api/kma_ultra_short_nowcast`의
obsr_value 기반 T1H/RN1/REH/WSD/PTY 실제 관측만 추가하며 일반 예보·외부 예측 NOWCAST는 제외한다.
W-MAP-04 구현은 성공 receipt/projection W-MAP-02가 계속 선행이고 미착수다. COW는 합산 실측
게이트 전 미채택이며 Weather/Transport 구현 소유권은 분리한다.

PR #132·#133 설계/관측 선택은 머지됐다. 현재 후속은 프록시 service 우회 방지·canonical replay/
실제 수집 시각·4단계 의존을 보강한 문서 HEAD의 2인 리뷰와 머지 판단이다. 구현 순서는
W-MAP-01 계약/프록시 → W-MAP-02 receipt → 성공 승격 → 실제 조회 공개이며 W-MAP-02 완료 전
데이터 공개 완료나 W5 성공 projection 선행 충족을 선언하지 않는다.
코드·schema·운영 변경은 없고 기능 구현도 착수하지 않았다. 문서 PR의 `[skip ci]`는 후속 기능 PR의 CI·2인 리뷰·배포 전 Map
응답 예시 전달·live E2E/API/UI·실측/배포 게이트를 생략하는 지시가 아니다.


## 2026-10-21 회수 sensor tick 예산·정체 경보 — 머지 대기

브랜치 `feat/recovery-sensor-budget-stall`. Common pin `e746e4b`, `weather_sync_recovery`에
`budget_seconds=40`·`on_stall`, 정체 경보 로그 `weather_sync_recovery.stalled`와 gauge
`ktw_sync_recovery_stalled`·규칙 `KorTravelWeatherSyncRecoveryStalled`. 자세한 것은
`docs/journal.md` 같은 날짜 절과 `docs/observability.md`. 배포(odroid, codex 관리)는 스키마 변경이 없어 migrate가 필요 없고, code-server 이미지(sensor가 도는
곳; `/opt/kor-travel-odroid/compose.yml`의 `kor-travel-weather-dagster-code-server` image 태그)와
같은 소스의 API 이미지를 새 main으로 재빌드·교체하면 된다. 공용 plane의 sensor gRPC timeout 60초 안에
예산 40초가 들어간다. odroid에는 weather Prometheus가 없어 새 규칙은 아직 평가되지 않는다 — 그동안
경보는 Dagster sensor 로그와 code-server `:14103`의 gauge로만 보인다. transport override 파일은 무관하다. 실패 중 sensor를 오래 멈췄다가
다시 켤 때는 재기동 전에 sensor cursor를 지운다(common runbook §19). daemon OFF는 유지한다.

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
PR #118의 배포 수용 기록이며 daemon OFF를 유지한다. 전체 수집·24시간 OOM/swap·자동 복구·Track1/2의 미검증
운영 수용을 완료로 바꾸지 않는다. 아래 PR #117의 RSS 미입증 문장은 당시
카탈로그 조회 범위의 기록이며 이번 격자 저장 실측과 구분한다.

## 2026-10-18 PR117 수집 카탈로그 개선 현재 수용

제품8c503fa8의 두 독립 소스 리뷰·exact-head CI·전체 Python663·frontend214/type-check·
Ruff·ARM64 API/CS native build를 통과했다. 실제 배포065661ca PASS 뒤
WSL Chromium1243 live UI36 PASS/exit0, role lifecycle 불변·daemon OFF를 확인했다.
[제품·이미지·원시 측정 및 수용 근거](collector-catalog-cpu-20261018.md)를 따른다.
ARM 고정 fixture CPU 중앙값은 약12.2%, Python 할당 peak는 약16.0% 감소했다.
RSS의 실질적 개선, 전체 provider 수집 성능, 장시간 안정성, Kakao 실제 fallback과
주소 전체 sweep, Track 1/2 미검증 운영 수용의 완료를 주장하지 않는다.
이 수용 기록은 PR #117에 포함하며 아래 pending 문장들은 해당 작성 시점의 이력이다.

## 2026-10-18 운영 배포·제한된 실제 수용 완료

Weather #115 배포와 migration0021을 완료했다. 기존 UI36(WSL)·새 주소 UI10(n150),
실제 제공자 단일 대상 갱신 및 원천 위치1,450행 보존을 확인했다.
[운영 결과와 미검증 범위](reviews/2026-10-18-address-live-deployment.md)를 따른다.
Daemon OFF를 유지하며 아래의 운영 미실행 문장은 각 작성 시점의 이력이다.


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


## 2026-10-18 주소 확인 집계 후속 종결

Common40·Weather113은 두 독립 FULL SOURCE PASS/CI 후 머지했다. 제품2bd779의 전체661·CI661,
새 pin의 loopback UI13과 원천 보존을 [최종 원문·수용 범위](reviews/2026-10-18-address-summary-final.md)에
보존했다. 운영 migration/provider/배포·수집 CPU/RSS와 Transport 별도 gate는 후속이다.
아래 구현 당시 대기 문장은 당시 이력이며 Daemon OFF를 유지한다.

## 2026-10-18 현재 원천의 주소 확인 시각 후속

Common40의 522525aed3db934767c1c9e742b3bfc68e91679c로 pin을 갱신한다. 비활성·좌표 이동 전의
파생 결과가 현재 최근 정상 확인 시각에 남는 공통 집계를 수정한다. Weather 실제 모델의
비활성/경도 이동/위도 이동 반례는 기존 pin에서 3 FAIL→새 pin의 주소 회귀 4 PASS다.
원천 주소·격자·관리자 metadata·과거 sidecar를 보존한다. 새 CI·두 독립 검토·UI가 후속이며
이전111/112의 PASS를 새 pin 검증으로 세지 않는다. Track1·2 전체 종료 뒤 수집 CPU/RSS를 개선한다.
Daemon OFF를 유지하며 운영 배포·provider 실호출은 이번 검증에서 실행하지 않았다.

## 2026-10-18 주소 갱신 구현·로컬 검증

VWorld 우선·Kakao fallback, 위치별 달력 6개월 자동 갱신과 Admin 수동 실행을 구현했다. 두 앱은 같은 Common Python 엔진을 고정 commit으로 사용한다. [설계·설정 및 검증 범위](reverse-address-refresh.md)를 참조한다. 원천 주소·좌표는 보존한다.

로컬 실브라우저 최종 재검증은 앱별 13개(합계 26개) 통과했고 실제 인증·상태 API·Dagster 큐 예약·중복 차단·모바일/키보드·오류 복구를 포함한다. 제공자는 주입 fixture다. Python 전체 검증은 Common 509개, Weather 658개, Transport 838개 통과·환경 조건 10개 skip이다. Common39·Weather111은 두 독립 리뷰/CI 통과 후 머지했다. [최종 리뷰 기록](reviews/2026-10-18-address-refresh-final.md)을 참조한다. Transport 정식 gate는 별도이며 운영 migration·실제 제공자 실호출·배포·운영 UI 수용은 미실행이다. Daemon은 중지 상태를 유지한다.

2026-10-17 main39aafe0/109까지 MERGED. 이 문서 PR 생성 전 OPEN PR0 시점의 정산이다. [감사](audits/2026-10-17-remaining-work.md)와 [남은 작업](tasks.md)을 읽는다.

root 11:58:07 UTC RO는 API8d2777b·CS8cdb78d healthy/restart0/OOMfalse·점검90/90초, UI기존 noHC, 전체77/16running·공유 daemon OFF를 확인했다. label을 전체 module 검증으로 바꾸지 않는다.109 UI36PASS, native retention run8d87945 SUCCESS/complete_snapshot·8필드 Observation1개는 완료된 한정 수용이다.

다음은 지역 freshness·정식 NVMe source/build 경로·consumer/관측 증거의 RO 정산이다. Manager 정본의 옛 probe와 운영 안전 override 차이는 공통 소유자가 후속 통합한다. 현재 운영 kill-probe라고 주장하지 않는다.

이미 있는 adapter/sampling/recovery/retention을 재구현하지 않는다. daemon 자동 복구/스케줄 start·새 SQL/DDL·초기정리 반복0. 별도 수집 재개 승인 뒤 장시간 자원·단일 retry 소유권·정기 처리량을 수용한다.

이전 백로그와 당시 상태는 deploy/n150.md에 그대로 남기며 vendored provider 문서는 편집하지 않는다. 이번 docs4개 commit은 사용자 승인 [skip ci]이고 full suite/새 build·live UI0, 독립2문서 리뷰 후 root merge한다.
