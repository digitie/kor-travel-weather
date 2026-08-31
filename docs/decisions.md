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
