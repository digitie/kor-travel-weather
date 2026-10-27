# Decisions index

날씨 프로젝트에서 이식·확장한 핵심 결정을 한 곳에 모은다.

- [ADR-002] 비동기 provider I/O와 API/repository 경계
- [ADR-020] admin UI를 별도 package로 유지
- [ADR-048] versioned REST envelope와 pagination
- [ADR-062] generic weather source와 3년 history 보존 목표
- [ADR-072] `known_at`/`target_at` bitemporal 축
- [ADR-074] append-only write safety와 disabled lifecycle
- [ADR-089] current summary fact reference, source revision, rebuild receipt
- [ADR-101] KMA 특보를 정식 weather bundle dataset으로 수집·표시한다. 기존의
  “후속 추가” 결정은 KMA alert adapter, hourly asset, API bundle 도입으로
  대체되었으며, 지역별 발령기관 매핑·철회 reconciliation은 운영 hardening으로
  추적한다.
- [Manager ADR-58] (proposed, 미구현, 2026-10-13) weather 스택을 odroid 호스트
  (`192.168.1.13`)에서 운영한다 — 포트·컨테이너명·경로는 n150과 동일하고 host만
  `.14 → .13`; odroid 전용 PG와 자체 Dagster plane; 데이터는 fresh 재적재; 관측은
  n150 Prometheus가 odroid `14104`를 federate하는 1단계. 정본은
  kor-travel-docker-manager `docs/decisions.md` ADR-58이며 이 저장소는 자체 ADR
  번호를 만들지 않는다(이 인덱스의 초기 항목 002~089는 Map `docs/adr/` 번호를
  거울하지만 101부터는 이 저장소 고유 번호다 — Map ADR-101은 다른 주제; 교차
  저장소 결정은 번호를 새로 만들지 않고 저장소명을 붙여 둔다). weather 쪽 반영은 `deploy/n150.md` "계획: odroid 호스트",
  `docs/observability.md`, `docs/integration-map.md`, `docs/runbooks/docker-app.md`.
- [소유자 결정 2026-10-27] (Weather 저장소 운영 결정, 새 ADR 번호 없음, 소유자 "권장대로")
  - **export 목표 일시중지.** Map 서비스 export 목표를 소유자가 멈췄다. Map의
    `weather_state_*` schedule(locations 제외)과 PinVi weather flag는 꺼 둔 채로 두고,
    KHOA 해수욕장 한 곳의 catalog 좌표 수정은 재개 때까지 미룬다.
  - **외부 날씨 current는 job을 나눠 앞당긴다.** openweathermap·weatherapi·open_meteo는
    current와 forecast를 한 Dagster run에서 받아, 승격이 run 전체 SUCCESS를 기다리느라 current가
    약 1시간 늦게 공개된다. export 재개 시 current와 forecast를 **별도 job으로 분리**한다.
    "run SUCCESS 뒤에만 승격" 규칙은 유지하고, step 단위 승격 판정으로 바꾸지 **않는다**.
  - **artifact GC는 dry_run으로 시작해 관찰 뒤 delete.** PR #176의 export artifact GC는
    `dry_run`으로 배포한다. 약 1주(목표 2026-11-03) `ktw_export_artifact_gc_reclaimable_bytes`와
    publisher tick의 `gc` 단계 시간을 보고, 배타 guard 안 참조 재확인이 2초보다 충분히 짧게
    유지되면 `delete`로 바꾼다. 디스크 여유는 약 187 GB다.
  - 추적: `docs/tasks.md` 2026-10-27 소유자 결정 절. odroid 현재 release 표지는 별도 제안
    ([Codex 합의 필요](reports/2026-10-27-odroid-weather-current-release-pointer.md))이며 결정이 아니다.
- [ADR-102] (2026-10-27) W5 `daily-summaries` 전체 목록(location_id 없음)은 한 번도
  승인되지 않은 위치별 관측 원천(선언된 cohort 대상·알려진 관측 anchor) 때문에 503을
  내지 않고 stale로 표시한다. 그 위치는 승인된 원천만으로 요약하거나 null/sample_count=0이며
  `source_providers`로 기여 원천을 구분한다. 단일 위치 최초 조회는 필수 원천 미승인이면
  503을 유지하고, 선택 원천(청정넷)은 503을 내지 않는다. dataset 전체가 한 번도 승인되지
  않은 경우와 승인 관측의 reducer 미도달은 기존대로 이전 집합 stale/최초503이다. W5 지표가
  없는 AirKorea는 W5 필수 원천에서 뺀다(W2 current 범위는 그대로). 이유: 측정값 없는
  측정소·응답에서 빠지는 지점은 구조적으로 승인되지 않아 전체 W5가 영구503이 됐다.
  `docs/reports/2026-10-23-daily-source-eligibility.md`의 전체 목록 '최초503' 문장을 대체한다.
- [ADR-103] (2026-10-27) export snapshot handle 보존: publisher tick이 `expires_at`이
  6시간 넘게 지난 `weather_export_snapshots`를 tick당 최대20개 지우고 refs는 CASCADE로
  지운다(설정이 아닌 모듈 상수. current handle 하나의 ref가 수천 행이라 5초 statement
  상한 안에 끝나도록 작게 둔다). 유효 cursor(만료 전)는 지우지 않는다. 첫 페이지가 재사용한
  handle은 TTL을 연장하므로 계속 요청되는 selection의 최신 handle은 남는다. 대신 6시간 넘게
  요청이 없던 selection은 이전 성공 집합 fallback(W1 축소 방지 비교 포함)을 잃는다.
  이유: manifest 변경·TTL마다 수 MiB handle과 scope별 refs가 쌓이는데 지우는 경로가 없었다.
  설계 메모는 `docs/design/map-service-exports.md` §3.1 정산을 따른다.
