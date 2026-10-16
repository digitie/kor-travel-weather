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

## 예약 시작 분산

정확한 시각보다 수집량·신선도·복구 가능성이 중요하다는 사용자 지침에 따라 외부
provider의 동일 분 예약을 분산한다. [Common PR #31](https://github.com/digitie/kor-travel-common/pull/31)의
`kortravelcommon.scheduling.staggered_cron`을 고정 commit으로 채택한다.
Weather는 provider별 고정 phase를 소유하며 catalog 순서나 활성화 여부로 다시 계산하지 않는다.

기존 아홉 provider가 `15 */3 * * *`에 함께 예약되던 것을 3시간 안에서 7분부터
20분 간격으로 나눈다. provider마다 3시간 간격·하루 8회, 전체 하루 72회 **예약**을 유지한다.
같은 분에 시작할 예약은 9개에서 1개로 줄어든다. 실행이 3시간보다 길면 기존 coalescing이
다음 tick을 합치므로 하루 8회 완료나 데이터 나이 3시간 이내를 보장한다는 뜻은 아니다.

provider의 HTTP 재시도·요청량·대상 수·source identity·게시 chunk와 실행 상한,
run-group 동시성 제한은 바꾸지 않는다. KMA 발표 시각과 base 선택도 변경하지 않는다.
retention은 기존 저부하 시각을 유지하되 새 분산 예약과 겹칠 수 있음을 코드에 명시한다.
분산 후의 운영 CPU·RSS·실제 수집 완료 시간과 retention 경합은 아직 미실측이다.

새 회귀는 실제 Dagster 1.13.24 달력으로 월말을 포함한 2일을 열거해 provider별
16회·3시간 간격, 전체 시작 간격 20분·동시 시작 1개를 확인한다. 기존 소스에서는
동시 시작 9개로 실패하고 후보에서는 통과했다. 최초 old 비교는 테스트의 conftest가
후보 경로를 다시 주입해 유효한 old 검증이 아니었으므로 집계하지 않았다. 격리된 복사본에서
실제 import 경로가 기존 소스임을 확인한 뒤 실패를 재현했다.

후보의 전체 Python 473개와 프론트엔드 140개, Ruff·프론트엔드 타입 검사가 통과했다.
소스의 두 독립 리뷰·운영 배포와 실제 수집/복구·live UI는 후속 gate이며 완료로 세지 않는다.
