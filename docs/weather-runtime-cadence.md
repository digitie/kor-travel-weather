# Dagster 시작 비용과 예약 분산 개선

## 그래프 재사용

작업별로 전체 asset graph를 다시 만드는 대신 프로세스마다 한 번 만든 graph와
resource **정의**를 재사용한다. 실행 중인 provider client나 DB 연결을 캐시하지 않는다.
최종 `defs` 외에 module 수준의 `Definitions` 객체를 추가로 노출하지 않는다.
작업 이름·선택 자산·실행 태그·executor·예약은 유지한다.

2026-10-15 Linux x86의 새 Python 프로세스에서 기존 코드와 후보를 교차 순서로
각 3회 측정했다. Dagster 자체 import 이후 Weather 정의 import와 repository 해석을
측정했으며 모든 작업의 이름·노드·태그·executor와 모든 예약의 이름·cron·timezone·기본
상태를 비교해 동일함을 확인했다.

| 중앙값 | 기존 | 후보 |
| --- | ---: | ---: |
| graph 해석 호출 | 17 | 1 |
| 해당 구간 CPU | 0.151초 | 0.012초 |
| Weather 정의 전체 CPU | 1.915초 | 1.725초 |
| Weather 정의 전체 경과 | 1.929초 | 1.734초 |
| Python 추적 peak | 36,291,306 bytes | 35,442,286 bytes |
| 프로세스 peak RSS | 152,800 KiB | 152,832 KiB |

이 결과는 로컬 정의 로딩 비용이다. 운영 ARM CPU 점유율이나 수집 완료 시간 개선을
증명하지 않으며 RSS 감소도 확인하지 못했다. provider import·작업별 프로세스 격리와
다중 프로세스 executor는 유지한다. 후보의 Dagster·location 회귀 124개와 Ruff가 통과했다.

## 예약 분산 후속

정확한 시각보다 수집량·신선도·복구 가능성이 중요하다는 사용자 지침에 따라 외부
provider의 동일 분 예약을 분산할 예정이다. 현재 커밋에는 cron 변경이 없고,
수집량 보존·재시작 후 동일 위상·실제 부하·완료 시간 검증은 아직 실행하지 않았다.
