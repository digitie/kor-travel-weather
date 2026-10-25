## 2026-10-25 — 작은 UI 첫 묶음: Weather 톤·보조 안내·헤더 밀도

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
