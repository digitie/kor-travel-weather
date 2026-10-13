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
