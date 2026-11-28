# ADR-101: KMA 특보를 정식 weather dataset으로 수집한다

- 상태: 채택
- 결정일: 2026-08-30(저장소 생성 때 기록. 처음에는 "후속 추가"였다가 이 결정으로 대체됐다)

## 맥락

특보는 PinVi 여행 카드와 지도에서 날씨 값과 함께 보여야 한다. 특보를 별도 서비스나
소비자 쪽 직접 호출로 두면 수집·lineage·보존 규칙이 weather fact와 따로 놀고, 소비자마다
KMA 호출과 오류 처리를 다시 구현하게 된다.

## 결정

KMA 특보를 weather bundle의 정식 dataset으로 수집하고 API bundle로 제공한다. KMA alert
adapter와 주기 asset이 수집하고, 다른 weather dataset과 같은 source lineage·append-only
규칙을 따른다.

## 검토한 대안

- 특보를 "후속 추가"로 미루고 소비자가 KMA를 직접 호출: 소비자마다 인증·오류 처리·지역
  매핑을 다시 만든다.
- 별도 특보 서비스: 운영 단위가 늘고 weather fact와 lineage가 갈라진다.

## 결과

- 특보 표시는 weather API 한 곳에서 나온다.
- 지역별 발령기관 매핑과 철회 reconciliation은 이 결정의 범위가 아니라 운영 hardening으로
  추적한다. 발표 관측 이력 계약은 [`../pinvi-alert-events.md`](../architecture/pinvi-alert-events.md)를 따른다.
