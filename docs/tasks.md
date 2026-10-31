# Weather 남은 작업

## 2026-10-31 — 특보의 실제 구역 자료 연결

- [ ] **W-ALERT-ZONE-01 / P1** — 기존 KMA 특보 목록은 발표 관서·제목만 제공한다.
  공식 특보구역과 위치의 관계를 provider 라이브러리에서 연결하고 실제 구역별 적용을 검산한다.
  현재 API는 구역을 확인할 수 없는 특보를 위치에 적용하지 않고 미확인 안내를 반환한다.
  이를 실제 모든 구역의 특보 수용 완료로 확대하지 않는다. [판정 계약](reports/2026-10-31-alert-weather-separation.md).

## 2026-10-31 — 지역 대표값 운영 관측 후속

- [ ] **W-REGION-ALIAS-01 / P2** — 실제 지도에서 `경기`/`경기도`, `경북`/`경상북도`가
  별도 대표 그룹으로 나타난다. 행정구역 별칭을 정규화하고 동일 시도·시군구의 중복 대표값을 검사한다.
- [ ] **W-DETAIL-CURRENT-01 / P2** — 현재 일 최저/최고가 `/latest`에 없고 같은 날짜의 예보에만 있는 경우,
  날짜·제공처·발표 기준을 보존하는 명시적 보완을 검토한다. 관측 기온을 미래 예보로 바꾸지 않는다.

[운영 실제 화면과 미제공 경계](reports/2026-10-31-details-regional-live.md).

## 2026-10-29 — 최신 배포와 UI 수용 경계

API·웹·코드서버 `ad4ee535`와 schema0035·운영 표식 등록을 반영했다.
[실제 배포·복구·검증](reports/2026-10-29-latest-weather-deployment.md)을 따른다.
기능 재검증 15항목은 통과했고 최초 지도 조회의 HTTP504는 별개로 보존한다. 아래 **W-UI-CURRENT-01**의
최초 전체 조회·장시간 성능, retention 물리 정리, export 재개·Map 소비자 수용은 완료로 바꾸지 않는다.


## 2026-10-28 — 특보 후보 제한 한정 수용

W-MARKER-PREFIX-01의 원 후보 API-only 배포·첫60지점×3 수용은
[완료 목록](tasks-done.md#2026-10-28--특보-후보-제한-api-단독-배포와-첫-cold-수용)으로 옮겼다.
[운영 수용 범위](reports/2026-10-28-marker-prefix-deployment-acceptance.md)를 따른다.
아래 기존 마커·UI 과제의 이전 실패는 이력으로 보존하며 전국 전체·장시간 성능,
공유 plane 건강·retention·특보 지역 적용 정합까지 완료로 확대하지 않는다.

## 2026-10-28 — retention 현재 수용 경계

- [ ] **W-RET-COMPLETE-01 / P1** — 자연7 SUCCESS·8 materialization 21만facts 진척과 별개로 과거6h 물리 정리 완료·미래값 보존을 수용한다. 8 materialization은remaining/budget_exhausted이며 lag는위치상한이다.
- [ ] **W-RET-COST-01 / P2** — parent+16leaf retention index와 current PK는정상이다. current11index/INCLUDE4·NOT VALID FK17·통계추정만으로 비용원인을단정하지말고 한가지가설을별도검토된측정으로확인한다.
- [x] **W-UI-BASIC210-01** — 현재API210/웹5098의 기존 공개기본UI21·실제 인증/지도/목록·소유정리를 후속20.241초 실행으로 수용했다. 첫125실패·옛39이미지비증명은보존한다.
- [ ] **W-UI-CURRENT-01 / P2** — 2026-10-29 현재 ad4ee535의 기능15(상세·제공처·PC/모바일)는 별도로 통과했다. 최초 HTTP504·전국 마커/장시간 성능·공유 plane 장시간 검증은 미완이며, 옛 API210/웹5098의 기본21이나 cold 25초 수용과 합치지 않는다.

[실측·소스정합·원실패와한계](reports/2026-10-28-retention-catalog-and-current-source.md)를따른다. 아래W-OPS-04의첫자연SUCCESS는현재부분수용됐지만6h완료/유입비교는미완이다. export publisher는취소desired STOPPED로유지하며재개/GC설정전환은이번범위가아니다. 아래기록은각시점의이력이다.

## 2026-10-27 — Codex PR 리뷰 정리(W01~W15) 후속

리뷰 정리 PR(브랜치 `review/codex-10d-weather`)에 넣지 않고 남긴 항목이다. 상세 근거는 PR 본문.

- [ ] **W-OPS-08 / P0 (소유자 결정)** — 운영 `KOR_TRAVEL_WEATHER_ENABLED_PROVIDERS` 확인. 기본값의
  tomorrow_io·weatherbit·weatherstack·accuweather는 export 경로가 없어 `/current`·external·W5가 설계상
  503이다. Manager env 명시 또는 W1~W5·W4에 '실제 구성된 provider' 판정 도입(설계 변경) 중 결정.
- [ ] **W-MAP-06 / P1** — publisher 처리량·후보 단위 격리·잠금 없는 판정·tick 단위 Dagster 조회
  기억(리뷰 W08)은 #176(머지됨)이 고쳤다. 배포 뒤 운영 처리량과 pending 수·oldest age·결과별 gauge를
  실측한다.
- [ ] **W-MAP-07 / P1** — owner epoch fencing(F4): 봉인된 정확 성공 후보가 새 claim만으로 퇴역하는
  문제. export_heads.py(#175 수정 중)와 함께 설계한다. bind 잠금 범위(F9)·planless/unclosed 성공 run
  attempt 정리(F6 잔여)도 #175 머지 뒤.
- [ ] **W-MAP-08 / P2** — 신선도/상태 의미(F12/F30/F46), since delta tombstone(F14/F25), selection_key
  열+인덱스(F16), artifact guard 모드 분리(F10/F19/F20), 위치별 quarantine(F21/F31), 별도 problem code
  (F8/F23), 410 media type(F26), approved_locations GC(F35), winner 정책 revision(F36),
  per-metric sample count(F28), marker 특보 projection identity(F37).
- [ ] **W-OPS-09 / P2** — CI Playwright 실행(F44), KMA fill quota 기본300의 키별 허용량 확인(F47),
  metrics mmap 압축(F48), ValuesMissingHigh 경보 재보정(F49). #174 범위: retention 관측 이벤트(F50),
  0021 parity 시험(F51), 주소 문서(F52).

## 2026-10-27 — 소유자 결정: export 일시중지·current job 분리·artifact GC 전환

[결정 기록](decisions.md)을 따른다. 소유자 "권장대로"(2026-10-27).

- **상태 — export 목표 일시중지(소유자 2026-10-27).** Map `weather_state_*` schedule(locations 제외)과
  PinVi weather flag는 꺼 둔 채로 둔다. 다시 켜는 것은 소유자 재개 지시 뒤다. KHOA 해수욕장 한 곳의
  catalog 좌표 수정도 재개 때까지 미룬다. 아래 W-MAP-05·Map 소비자 수용은 재개 전까지 진행하지 않는다.
- [ ] **W-EXP-SPLIT-01 / P1 (보류 — export 재개 시)** — openweathermap·weatherapi·open_meteo의
  `<provider>_weather_job`이 current와 forecast를 한 run에서 받아, 승격이 run 전체 SUCCESS를 기다리는
  동안 current가 약 1시간 늦게 공개된다. current와 forecast를 **별도 job**(별도 schedule·run group)으로
  나눈다. "run SUCCESS 뒤에만 승격" 규칙은 그대로 두고 step 단위 승격 판정은 도입하지 않는다. 수용:
  current 승격 지연이 current job 소요 시간 수준으로 줄고, forecast 실패가 current 승격을 막지 않으며,
  provider 호출 수·quota가 분리 전과 같거나 적다.
- [ ] **W-EXP-GC-01 / P1 (목표 2026-11-03)** — PR #176 artifact GC를 `dry_run`으로 약 1주 관찰한 뒤
  `KOR_TRAVEL_WEATHER_SERVICE_EXPORT_ARTIFACT_GC=delete`로 바꾼다. 볼 것: `ktw_export_artifact_gc_reclaimable_bytes`
  추이와 publisher sensor skip 메시지의 `stages=…gc:<초>`. 전환 조건은 배타 guard 안 참조 재확인이
  2초보다 충분히 짧게 유지되는 것이다. `dry_run`은 guard 안 재확인을 실행하지 않으므로, 관찰 기간에는
  guard 밖 전체 참조 조회(재확인은 그 digest 부분집합)를 상한 대리값으로 보고 `delete` 첫 tick들에서
  실제 재확인 시간을 다시 확인한다. 디스크 여유 약 187 GB(2026-10-27)라 1주 대기에 여유가 있다.
- [ ] **W-OPS-ODROID-01 / P2 (Codex 합의 필요)** — odroid에 현재 weather release 기록이 없어 최신
  `images.yml` 없이 재생성하면 조용히 옛 이미지로 돌아간다(2026-10-27 weather173/176 배포가 엇갈림).
  `/srv/ktdm-releases/weather-current` 표지와 runbook 문구를 [제안](reports/2026-10-27-odroid-weather-current-release-pointer.md)했다.
  합의 전 odroid는 바꾸지 않는다.

## 2026-10-27 — 실제 마커 지연과 다음 배포 절차

- [ ] **W-MARKER-01 / P1** — #173의 배포/0034·PC375 지도 수용은 통과했으나 실제60×3
  본문19.506/29.884/28.324초로25초 상한 미달. 저장된 요청/시각과 SQL predicate/index/조인/정렬로
  병목 하나를 식별하고 최소 변경을 제안한다. 전체 전국 완료·빠른 응답 수용으로 세지 않는다.
  PR180의 무필터API 경로와 실제6종류기본지도 경로를혼동한 분석은
  [정정보고서](reports/2026-10-27-marker-disconnect-admission.md)로정정했다.
  입장전취소DB진입 최소수정은전체게이트와운영효과가남았으며25초목표를완료로바꾸지않는다.
- [ ] **W-DEP-02 / P1** — 빈 drain 반환·safe_to_restore 변경·정방향 변이 앞의 취소 safepoint를
  검증하고 배포 전 존재한 marker4 인덱스 baseline을 rollback에서도 보존한다.
- [ ] **W-DEP-03 / P1** — 장기 예보 자연 종료는 pause/G 전에 기다리고 최종 짧은 재확인 후
  교체해 실황/특보·publisher 일정 중지 구간을 줄인다. 현재 r3를 소급 변경하지 않는다.

[실제 배포·수용 실패·원장과 한계](reports/2026-10-27-weather173-deployment-and-latency.md)를 따른다.
새 current1197행은 자연 재개 확인이며 예보 전체 완주·이전 공백 자동 backfill은 아니다.

## 2026-10-26 — metrics scrape CPU 후속

- [ ] **W-METRICS-01 / P1** — [PR #171](https://github.com/digitie/kor-travel-weather/pull/171)의 per-preflight raw key 제한 메모 구현·새10회귀·합성 비교·최종 CI/독립2인 검토와95020 병합·공식3이미지/0033 배포·기존 listener HTTP200·공개 UI21은 수용했다. **운영 ARM CPU/RSS 개선·실제 memo-hit·장시간 수용은 미완**이므로 이 상위 비용 과제는 완료로 바꾸지 않는다. 원래21RUN/2STOP만 복원했고 Map 연동 신규 활성화0이다. [배포 수용과 한계](reports/2026-10-27-weather171-deployment-closure.md), [합성 측정](design/2026-10-26-metrics-scrape-key-validation.md).

## 2026-10-25 — 조사 기반 지도·날씨 정보 후속

[웹·Android/iOS 조사](research/2026-10-25-weather-ux.md)와 [설계·수용 기준](design/2026-10-25-weather-map-improvements.md)을 따른다.
기존 admin 메뉴·조회 구조를 유지하며 필요한 API만 보완한다. 첫 [작은 UI 묶음](reports/2026-10-25-weather-ui-compact.md)은
색상·안내·헤더를 부분 구현했으며, 아래 항목의 전체 수용을 완료로 세지 않는다.

- [ ] **C-MAP-01 / P0** — Common 담당 공유 작업: Map 기준 외곽·가용 높이·상세 슬롯/테마 계약·Map 회귀·패키지 인계. Transport와 같은 작업이며 별도 구현하지 않는다.
- [ ] **W-UX-01 / P0** — 톤·compact 헤더 PR #169 머지 완료. 공용 dev.8 작업면·남은 높이·필터/상세 접기·명시 확장을 구현·검증 중이다.
  [최종 로컬 실측](reports/2026-10-25-weather-map-workspace.md)의 Common `a74ac80`/pack `71d9f6…`·UI34·타일 주입10·타입/lint/253단위·package build는 PASS다.
  독립 리뷰의 타일 회복 안내·목록 첫행 가림 P2를 실제 앱으로 재현·수정했다.
  [PR #170](https://github.com/digitie/kor-travel-weather/pull/170)의 최종 Docker·CI·독립 리뷰와 양 소비자/Common 통합 수용은 남아 있다.
- [ ] **W-UX-02 / P1** — 출처 i·긴 메타 압축 부분 구현. 특보·선택 시각·이후 시간별 흐름 우선 상세, 관심 요소·관측/미래 분리 유지의 전체 수용은 남아 있음.
- [ ] **W-UX-03 / P1** — 기존 forecast/marker 계약, DB 밖 목적지 좌표→적합 저장 anchor/부재·대표성·필터 보강, 주소/실행/교통 연결·API 동일 시나리오 수용.

아래 기존 인덱스는 2026-10-17 main39aafe0 기준 이력이다. 현재 PR #118의 부분 수용과 남은 게이트는 아래 최신 절을 따른다. 문서 PR 생성 전 감사 조회에서 OPEN PR0이었다. [전체 감사·증거·선행 관계](audits/2026-10-17-remaining-work.md)를 함께 읽는다. 과거 이전 백로그는 [deploy/n150.md](../deploy/n150.md)에 보존한다. vendored python-airkorea-api의 tasks/resume/journal은 별도 provider 문서다.

코드·PR merge·실배포·운영 수용은 다른 상태다. 증거 미확인을 미배포 확정으로 쓰지 않는다. 현재 daemon 정책은 최신 사용자 지시에 따라 Geo·Weather·Transport RUNNING이다. 과거 OFF 기록은 당시 이력이며 현재 실행 지시가 아니다.


## 2026-10-24 Map 서비스 export — producer 구현 완료, 운영·소비자 수용은 별도

감사 기준 main은 `9aa0d339d016aaa80798c75de7115f14b043cbe5`(#164)다. W1~W5의
실제 collector/승인 head·현재/예보/상태/사전 요약 reader는 구현·머지됐다.
[현재 계약·PR 인계](reports/2026-10-23-map-service-api-handoff.md)를 따른다.
아래 source 완료 표시는 실제 운영 bootstrap·Map 소비·ARM 비용/GC 수용을 포함하지 않는다.

- [x] W-MAP-01 — 기존 X-Admin-Token 인증·DTO/OpenAPI/TS 및 웹 service 프록시 차단(#135).
- [x] W-MAP-02 제품 — receipt/계획·성공 head와 SDK/HTTP/KMA native 수집 연결,
  inventory·W1/W2 frozen reader·W3 외부/단기/중기 grouped reader 구현(#136~#160).
  root/epoch·실패 stale·미승인503·publication watermark를 유지한다. reader 수명 #162와
  source vector8,192/메타데이터 상한 #164를 포함한다.
- [x] W-MAP-03 — 사용자 승인에 따른 OBSERVED와 KMA 실관측 NOWCAST의 W5 관측 eligibility 선택.
  일반 예보·외부 예측 NOWCAST는 제외한다.
- [x] W-MAP-04 제품 — 승인 관측 기반 bounded 사전 요약/coverage/checkpoint와 REST,
  다중 제공처 eligibility·새 SDK station·교차 provider 누락/이전 roster stale를 구현(#148/#152/#163).
  [집계](reports/2026-10-23-daily-summaries.md)와 [다중 원천](reports/2026-10-23-daily-source-eligibility.md)을 따른다.
- [ ] W-MAP-05 — API·CS·publisher의 같은 false-default flag/공유 NVMe artifact 권한을
  실제 배포하고 승인 source별 정상 bootstrap·필요 요약 초기 투영을 수용한다. 운영 #130 관련
  배포 이력은 후속 #135~#164 전체 API 적용의 증거가 아니다.
- [ ] Map 소비자 수용 — 응답 예시/OpenAPI/PR 전달 자료는 작성됐으나 실제 담당자 수령·
  reader/Valkey·REST/UI 적용 완료는 별도 확인한다. W1 null/stale 최초 참조와 W3
  next_since 전체 페이지 완료, W5 세 구간·미계산 stale/503을 검증한다.
- [ ] 운영 비용/GC — 실제 configured source 수가 catalog/cohort5,000·vector8,192·inventory20,000
  및 byte 상한을 만족하는지 확인한다. 같은 입력의 ARM CPU/RSS/PG WAL/latency·writer 영향,
  미처리 generation/checkpoint/frozen cursor live-root 보존·GC/파일 pin 수용 전 자동 ON하지 않는다.

아래 감사·인덱스는 당시 기준선이며 이미 구현된 W1~W5를 다시 만드는 목록이 아니다.

[2026-10-23 API 남은 작업 감사](audits/2026-10-23-map-api-remaining.md)를 함께 읽는다.

## 완료한 한정 범위

- [x] #107/#108 수집 CPU/복사 경로 개선 source MERGED. 실제 이미지/UI 수용과 동일 workload 전체 수집 성능 측정은 구분한다.
- [x] #109 source MERGED/CI 성공·CS 최신 이벤트 모듈 배포·UI36PASS·native retention complete_snapshot/영속8필드 Observation1개 확인. 정기 스케줄 완주 보증은 아니다.
- [x] KREX/KRForest adapter·산악 gate/streaming/공유 잠금, Common executor·분산예약/recovery, sampling/health 코드 존재와 머지 확인. 실제 최신성·복구/장시간 부하 수용은 별도다.

## 남은 단계

- [ ] W-OPS-01 / P1: 휴게소·산악 개별 native run/관측 최신성·누락 원인 RO 정산.
- [ ] W-OPS-02 / P1: NVMe canonical source/build 경로·consumer/관측 효과 증거 정산. source probe 부재와 대체 경로 미확인을 구분한다.
- [ ] W-DEP-01 / P1: Manager 정본의 옛 Weather probe와 실제 안전 override drift를 공통 소유자가 최신 main에 통합. #486/#488의 구 head를 blind merge하거나 현재 운영이 kill-probe라 단정하지 않는다.
- [ ] W-OPS-03 / P2: native/Common 단일 retry 소유권·선택 센서 상태·첫 평가/중단재개 수용. OFF에서 자동 활성화하지 않는다.
- [ ] W-OPS-04 / P2: 별도 승인된 재개 뒤 정기6시간 정리 처리량/deferred·미래 보존/진행 이벤트 수용.
  2026-10-27: 운영 job이 10-20 22:50Z 마지막 성공 뒤 상시 FAILURE(최근7일 FAILURE835)임을 읽기 전용으로 실측했다.
  원인(인덱스 머리 dead 재탐색·진척에도 Failure)과 수정(검사한 위치 이어 읽기·ctid 삭제·예산150초 설정·
  진척0/뒤처진 채 7회 연속 미감소만 실패)은
  [PR #174](https://github.com/digitie/kor-travel-weather/pull/174)와 [보존정책 2026-10-27 절](shortterm-retention.md#2026-10-27-상시-실패와-이어-읽기).
  남은 수용: 배포 뒤 첫 run SUCCESS·`resumed` 전환·`backlog_lag_seconds` 감소와 처리량/유입 비교.
- [ ] W-OPS-05 / P2: 같은 workload CPU/RSS·3h/12h 포함24h OOM/swap·실제 memory 정책 수용. PR #118의 고정 격자 입력 RSS 약15% 감소·DB 포함 CPU 동등 및 복구2건은 아래와 같이 부분 수용했으며 전체·장시간 조건은 남는다.
- [ ] W-DOC-01 / P2: 실제 경로/효과 증거 확정 뒤 옛 runbook 현행 절차만 최소 갱신. T-WEA-03/04는 조건부 후보로 유지한다.
- [ ] W-OPS-06 / P2: 회수 sensor 예산·정체 경보(2026-10-21 PR) 배포 뒤 수용. code-server 재빌드, 규칙 reload, `ktw_sync_recovery_stalled` series(dagster job, 0) 존재, sensor tick이 `stall:null` cursor로 성공하는지 확인. daemon 재개는 별도 승인.
- [ ] W-OPS-07 / P2: 외부 provider run group 분리(2026-10-21 PR) 배포 뒤 수용. 공용 instance `tag_concurrency_limits`에 `kortravelweather/run_group=external_weather_slow` `limit: 1` 추가(Manager), slow 항목을 code-server 갱신 **전에** 넣는다(없으면 slow run이 group 상한 없이 돈다). 갱신 뒤 fast provider run이 `open_meteo` 진행 중에도 바로 시작하는지, 외부 run이 최대 2개(fast 1 + slow 1) 겹칠 때 odroid CPU·load가 견디는지 확인.

2026-10-17 당시 작업은 docs4개뿐이었다. 당시 사용자 승인 [skip ci] 문서 검토 범위이며 현재 PR #118의 제품 검증을 뜻하지 않는다.


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
PR #118은 머지·배포·UI 수용을 완료했으며 daemon OFF를 유지한다. 전체 수집·24시간 OOM/swap·자동 복구·Track1/2의 미검증
운영 수용을 완료로 바꾸지 않는다. PR #117의 RSS 미입증 문장은 당시
카탈로그 조회 범위의 기록이며 이번 격자 저장 실측과 구분한다.

## 2026-10-19 지역 관측 정산 진행

휴게소·산악 최근 성공 native 실행과 실제 저장 행을 대조했다. [개별 제외 원인과 최신성 한계](audits/2026-10-19-regional-acceptance.md)를 따른다. 좌표 없는 응답과 관측값 없는 응답은 별도로 집계한다. 당시 자동 재개·신규 live 수집 수용은 대기 중이었다. 신규 두 asset 수동 수용은 아래 후속 기록으로 완료했고 자동 재개는 보류한다.

지역 제외 집계 제품683e446d의 전체668·frontend214·CI·두 독립 소스 리뷰와 운영 API/CS 배포·UI36은 완료했다. 이 배포 수용과 후속 실제 신규 지역 수집·자동 주기 검증은 구분한다.


## 지역 수동 수집 후속 수용

[수동 지역 수집 수용](audits/2026-10-19-regional-live-collection.md)을 완료했다. 첫 probe 실패와 두 번째 DB6403건 저장 뒤 native IO 실패를 보존했고, 보강 후 r3의 두 asset native SUCCESS·DB 연결6403건/신규5256건과 수집 후 UI36 PASS를 확인했다. 휴게소 신규0건은 정상 재수집이며 산악은 좌표59·빈값16 제외를 실측했다. 제품 변경과 daemon 재개는 없다. 전체 주소 수동 갱신은 미검증, 예약 주소 갱신·보관·자동 복구·장시간 전체 수집은 daemon OFF 제약 아래 별도 수용으로 남는다.

## 2026-10-23 KMA 페이지 완결성 선행 보강 마감

KMA PR32와 Weather PR141을 머지했다. public PaginationReceipt를 lineage에 보존하고
불완전 페이지가 성공 staging으로 넘어가지 않게 한다. [검증·키 전환·한계](reports/2026-10-23-kma-pagination-contract.md).
최종 CI/두 리뷰·로컬PG788/frontend243·격리API3/UI11이 통과했다. W-MAP-01/02의 실제
collector/head·정상0·비용/frozen·W3/W5·운영 수용을 완료 처리하지 않는다.

## 2026-10-23 W-MAP-02 단계별 수용 정산

- 3B 내부KMA실황성공head는PR143 main476c9f11로수용(전체PG812·정확CI·2리뷰·격리API/UI).
- 실제nonempty collector opt-in/별도정확성공publisher는PR144 main089bdc32로수용(정확CI전체·집중33·2리뷰·격리API/UI). 기본OFF다.
- W-MAP-02 전체는계속미완이다. strict8category/실제KMA W1/W2 frozen reader는PR145 진행중이며, 모든provider/current·forecast W3·상태W4·사전요약W5·GC/비용gate·Map소비/운영수용은남는다. 위초기진행이력의3B미착수표현은이정산으로갱신한다.
