# ADR-105: KMA 단기예보는 지난 대상시각 기준으로 정리하고 미래 예보는 보호한다

- 상태: 채택
- 결정일: 2026-10-16

번호 주의: Map에도 ADR-105가 있다. 다른 저장소의 ADR은 항상 `Map ADR-105`처럼 저장소명을 붙여 부른다.

## 맥락

KMA 단기예보는 발표마다 같은 대상시각을 다시 예보하므로 fact가 가장 빨리 쌓인다. 기존 전역
보존은 `known_at`(수집 시각) 일별 partition 단위라, 오래전에 발표됐지만 아직 오지 않은 대상시각의
예보까지 함께 지울 수 있었다. 공용 PostgreSQL의 디스크 여유는 작다.

## 결정

- 대상은 `python-kma-api`의 KMA 단기예보(`kma_short_forecast`, style `short`)뿐이다.
- 대상시각 `valid_at`이 현재보다 6시간 넘게 지난 행만 정리한다. 발표 시각(`issued_at`)이나
  수집 시각(`known_at`)의 나이로 지우지 않는다. 경계와 같은 행과 미래 예보는 보존한다.
- 모든 정리 경로(수집 중 제외, 전용 정리 job, 전역 일별 partition 정리, 수동 DEFAULT 정리)가
  같은 판정을 공유하고, 보존 대상이 남은 partition은 DETACH/DROP하지 않는다.
- source/raw lineage는 지우지 않는다. 지운 fact를 다시 수집한 경우의 `known_at` 처리는
  [ADR-072](072-weather-bitemporal.md)의 좁은 예외를 따른다.

## 검토한 대안

- `known_at` partition DROP만 사용: 빠르지만 미래 대상시각 예보가 함께 사라진다.
- 발표 시각 기준 정리: 오래된 발표의 미래 예보가 사라진다.
- 정리하지 않음: 단기예보가 저장량 대부분을 차지해 디스크가 버티지 못한다.

## 결과

- 지난 대상시각의 단기예보 이력은 6시간 뒤 사라진다. 지난 예보의 정확도 비교 같은 분석은 이
  dataset으로 할 수 없다. 되돌릴 수 없는 삭제이므로 이 기준을 바꾸려면 새 ADR이 필요하다.
- partition DROP 대신 행 단위 batch 삭제를 하므로 정리 job과 인덱스 운영 비용이 든다.
- batch 크기·예산·실패 판정 같은 운영 값은 [`../shortterm-retention.md`](../architecture/shortterm-retention.md)가 정본이다.
