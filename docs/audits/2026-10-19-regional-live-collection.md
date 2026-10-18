# 지역 수집의 수동 실행 수용

제품 #119의 배포·UI36 이후 휴게소와 산악 관측을 한 번씩 직렬 수집하는 별도 수동
수용을 진행했다. 제품 코드는 683e446d로 유지하고, 문서 기준 main은 #119 머지
fd2bf1a3이다. daemon은 OFF이며 대기 큐3개를 실행하지 않는다.
이 기록은 전체 regional job, 예약 주기 또는 daemon 자동 복구 수용을 의미하지 않는다.

## 시도와 실패 구분

| 시도 | 제공자·DB | Dagster 결과 | 판단 |
| --- | --- | --- | --- |
| r1 | 실행 전 | 사전 probe 실패 | 독립 metrics 디렉터리가 없어 definitions import 중단 |
| r2 | 두 수집 DB 성공, 총6403건 저장 | FAILURE | 결과 파일의 handle_output이 읽기 전용 경로에서 실패 |
| r3 | 최종 증거 대기 | 미정 | root가 수집한 종결 증거 후 갱신 |

실패 native run을 삭제하거나 성공으로 바꾸지 않았다. r2의 DB 저장은 이후 감사로
직접 확인했으며 Dagster FAILURE와 별개로 보존했다. 자동 재실행은 하지 않았고,
실패 원인 수정·독립 검토 후 별도 소유 시도를 사용했다.

## r2 실제 저장과 관측 시각

시각은 UTC다. DB 감사 시각은 2026-10-18 22:50:42.640445Z이다.

| 데이터 | 원천 | 연결 사실 | 신규 저장 | 관측 최소·최대 | 감사 시점 나이 |
| --- | ---: | ---: | ---: | --- | ---: |
| 휴게소 | 187 | 1147 | 1147 | 둘 다 2026-10-18 21:00Z | 6642.640445초 |
| 산악 | 438 | 5256 | 5256 | 둘 다 2026-10-18 22:40Z | 642.640445초 |

두 DB sync 상태는 success이며 run-source 연결로 확인했다. 반복 fact identity와
빈 raw source/fact payload는 모두0이다. 이 값만으로 제공자가 약속한 최신 관측이라고
판정하지 않는다. 수집 성공, 신규 저장, 실제 관측 시각·나이는 서로 구분한다.
r2는 IO 처리 뒤 materialization에 도달하지 못해 native asset metadata가 비어 있다.
따라서 r2에서 새 제외 counter나 예산 잘림 여부까지 실측했다고 주장하지 않는다.

## 실행 도구 보강과 사전 검증

실제 code server와 같은 public Dagster SDK1.13.24를 사용하고 두 asset만 선택했다.
제품의 max_concurrent1, 제공자 timeout20초, regional_max_records1000,
max_values_per_run8000000은 변경하지 않았다. worker 제한은 CPU1·메모리1GiB,
임시 파일용 tmpfs128MiB이며 NVMe의 소유 디렉터리에 증거를 저장했다.

별도 bootstrap이 instance 환경을 주입한 뒤 multiprocess metrics 디렉터리를 소유
tmpfs로 분리하고 metrics listener 환경키를 제거한다. instrumentation은 유지하면서
기존 CS의 metrics 파일·포트를 사용하지 않는다. 결과 파일·compute log는 소유 NVMe
디렉터리에 저장하고, 수집 전에 실제 SDK 경로가 그 하위인지와 쓰기 가능 여부를 검사한다.
기존 서비스 설정과 읽기 전용 루트 파일시스템은 유지한다.

감사에서는 known_at과 run.started_at을 비교하지 않는다. 제품은 fetched_at을 먼저
정하고 run을 만들기 때문에 정상 사실도 시간조건에서 제외될 수 있다. 실제 PostgreSQL
fixture에서 이 반례와 동일 payload 재수집을 검증했다. 새 저장0이지만 유효 원천·사실이
이미 연결된 경우는 already_current로 구분한다. native와 DB의 원천수·신규 저장수를
대조하며 빈 응답·skip·잘림·상한 도달은 정상 noop과 구분한다.

로컬 회귀21개가 통과했다. 실제 읽기 전용 Docker의 SDK 검증에서는 state mount가
없을 때 결과 쓰기 실패, 소유 mount가 있을 때 artifact와 compute log 생성 및 SUCCESS를
확인했다. 이 로컬 fixture를 운영 수집 성공으로 대신하지 않는다.

## r2 측정 범위

수집·결과 감사 구간 wall90.883초, worker cgroup CPU67.266초,
worker cgroup peak265404416바이트였다. parent RSS peak154620KiB와
종결 자식 RSS peak169116KiB는 별도 high-water이며 합산하지 않는다.
같은 구간 공유 PostgreSQL CPU는25.437초였고 다른 SQL을 포함한다.

CPU는 ready/go부터 result/release까지로 import 준비를 제외하고 결과 감사를 포함한다.
cgroup 메모리 peak는 import부터의 절대 high-water다. 실패 시도1회의 절대값이므로
성능 개선율·호스트 전체 CPU·장시간 메모리 안정성의 근거로 사용하지 않는다.

[공개 요약 증거](2026-10-19-regional-live-collection.json)는 비밀·제공자 raw payload 원문·
실행 도구의 개인 경로를 제외했다. r3 종결 후 독립 문서 리뷰를 거쳐 최종 수용을 정산한다.
