# Weather 실행기·공용 복구 센서 채택

## 소스 범위와 현재 상태

기준은 Weather97 `1c968f324e204d632a9588794de0e9e07a424d0d`이며
Common `eaf83e965ee837a277d5d7b370ecde0d4897f1cd`를 정확히 고정한다.
17개 job의 자산 선택·태그·run timeout, 17개 예약의 cron/KST·상태,
resource와 기존 `weather_sync_recovery` 앱 DB 정산은 유지한다.
이 문서는 소스 채택과 후속 운영 조건을 구분한다. 이미지 제작·배포·센서 활성화와
동일 작업 입력의 CPU/RSS/PSS 개선은 아직 검증하지 않았다.

## 실행기와 프로세스 경계

16개 단일 자산 job은 `in_process_executor`로 실행한다. 같은 run 안에서 별도
step child를 시작하는 비용을 줄이려는 변경이며 HTTP 요청 병렬도나 run 동시성을
높이지 않는다. 4개 자산의 `regional_weather_job`과 암묵적 전체 자산 job은
`multiprocess_executor(max_concurrent=1)`을 사용한다. 지역 수집 단계마다 process
정리 경계를 유지한다.

Dagster 1.13.24의 DefaultRunLauncher/user-code server는 여전히 각 run을 별도
spawn run worker에서 실행한다. 다른 run의 프로세스 격리, daemon의 태그 기반
최대 실행시간·취소·run monitoring은 유지한다. run worker 내부 실행이므로
프로세스 강제 종료가 아닌 정상 취소와 물리 worker 종료 확인이 필요하다.
단일 자산의 provider 호출·transaction·멱등 upsert·raw lineage는 변경하지 않는다.
끝나지 않은 provider thread를 건너뛰며 같은 run 안에서 재사용하는 정책도 추가하지 않는다.

기존에 저장한 run config가 multiprocess 전용 `execution.config`를 명시했다면 새
단일 자산 실행기와 호환하는지 배포 전 확인해야 한다. 기존 partial 재실행을 전체
job으로 확대하지 않는다. 장기 run의 자연 완료 또는 기존 승인된 SAFE_TERMINATE
정산·물리 drain 뒤 교체하고, 기존 PID 분류가 step child 부재를 worker 부재로
오인하지 않는지 확인한다. run worker의 spawn 프로세스는 남는다.

## 인프라 재시도: 한 센서, 한 소유자

`KOR_TRAVEL_WEATHER_INFRA_RETRY_ENABLED`는 기본 `false`이며 정확한 `true` /
`false`만 받는다. `true`이면 공개 `infrastructure_retry_sensor_for_jobs`를
통해 `weather_infrastructure_retry` 하나를 RUNNING 기본 상태로 등록한다.
17개 job mapping을 한 FAILURE page/cursor로 조회한다. 17개 센서를 추가하지 않는다.
기준 소스에는 인프라 재시도 센서가 없었으며 기존 센서 17개를 교체한 변경이 아니다.

현재 instance 설정은 `run_retries.enabled=true`, `max_retries=0`이다.
각 job의 기존 `RecoveryPolicy.tags()`가 `dagster/max_retries=1`과
`dagster/retry_on_asset_or_op_failure=false`를 이미 제공한다.
SDK 1.13.24는 run 태그를 instance 기본 상한보다 우선하므로 인프라 재시도 예산은
1회이며 op/provider 실패는 재시도 대상이 아니다. Common 센서는 native가 켜져 있으면
새 child 발급을 native에 위임하고, 자신이 전에 기록한 pending 인계만 마무리한다.
native OFF에서 Common이 발급하면 parent와 child의 잔여 native 예산을 0으로 만들어
OFF→ON 전환 시 중복 소유를 막는다. instance 정책은 이 변경에서 바꾸지 않는다.

수동 재시도와 daemon의 run-group 조회는 원자 CAS가 아니다. SDK는 이미 관측한
수동 child를 예산에 포함하지만 동시 수동 발급과의 경합까지 전체 child 1개를
절대 보장하지 않는다. 같은 run을 두 센서·수동 복구에서 동시에 인계하지 않는다.
Common은 명시적 infrastructure failure만 처리하며 partial asset/op/plan,
선행 provider 실패, 이미 있는 child·active run, 비멱등 policy를 제외한다.
앱 DB 정산 센서는 native run을 새로 실행하지 않으므로 역할을 혼동하지 않는다.

**운영 완료 gate:** 새 Common이 설치된 이미지와 17개 실제 태그를 확인하고
`KOR_TRAVEL_WEATHER_INFRA_RETRY_ENABLED=true`를 실제 CS 환경에 반영한다.
등록·실제 상태·native 소유권·첫 평가를 확인하기 전에는 재시도 채택을 완료했다고
표시하지 않는다. 기존 동일 목적 센서가 별도로 있다면 중복 RUNNING 상태를 해소한다.
실패를 만들기 위해 운영 provider/job을 고의 실행하거나 실패시키지 않는다.

## 선택적 native incarnation 정산

`KOR_TRAVEL_WEATHER_NATIVE_RECOVERY_REPOSITORY_ORIGIN_ID`가 없거나 비어 있으면
등록하지 않는다. 값이 있으면 공백을 임의 보정하지 않고 정확한 origin으로
`weather_native_incarnation_recovery`를 등록한다. 기본은 STOPPED다.
이 센서는 구 incarnation의 STARTED를 공식 FAILURE 이벤트로 정산하며 child를
직접 발급하지 않는다. 후속 retry는 앞 절의 한 소유자가 담당한다.

활성화 전에는 실제 DefaultRunLauncher, 정확한 repository origin, 단일 CS의
독점 origin/endpoint를 확인한다. rolling/blue-green/LB overlap을 허용하지 않으며
이전 init identity·worker PID/start ticks와 해당 cgroup의 소멸을 서로 확인한다.
컨테이너 내부 PID namespace·origin ID만 같다는 것은 이전 worker 사망 증거가 아니다.
UNKNOWN이면 STOPPED를 유지한다. 센서는 다른 namespace와 LB routing을 관측하지 못한다.

최초 tick은 incarnation/cursor만 기록하며 clock 변동·알 수 없는 launcher·불완전한
grpc origin은 fail-closed한다. 기존 cursor를 임의 초기화해 정산을 강제하지 않는다.
현재 살아 있는 run은 정상 재시작 대상이 아니다. 새 retry 센서를 켰다는 이유로
native 정산 센서까지 자동 start하지 않는다.

## 검증과 후속 측정

격리 시험은 루트 DB conftest를 로드하지 않으며 자체 임시 SQLite metadata만 사용한다.
실제 SDK로 실행기·등록 상태·17개 run 태그의 native 예산과 Common의
eligible/partial/provider/manual-child/native-owner/비멱등 분기를 확인한다.
기준 git object와 새 정의를 별도 Python process에서 읽어 job 태그·자산 선택,
예약·resource·기존 정산 센서가 동일함을 비교한다.

운영 배포 전후에는 동일 provider/지역/config·발표시각 범위에서 수집량,
source/fact lineage·freshness·취소 정합성, 전체 run worker/step child 수,
CPU 시간·RSS/PSS와 wall 시간을 측정한다. 감소 효과가 아직 실측되지 않았으므로
step spawn 제거를 실제 host CPU 감소 수치로 대신하지 않는다.
