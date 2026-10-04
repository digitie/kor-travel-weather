# Dagster 실패·정지 복구

## 확인된 문제와 변경

기준 main `5da6e15`는 run monitoring과 외부 provider 그룹 제한을 이미 갖고 있었지만,
같은 job의 이전 run이 남아도 스케줄을 계속 큐에 넣었다. worker 중단 후 남은 수집 행은
다음 수집이 시작되어야 lease로 회수했다. 모든 job은 16시간 상한을 공유했다. 외부 fetch
timeout 뒤 아직 살아 있는 thread와 다음 dataset/close가 같은 client를 사용할 수도 있었다.

common의 `coalescing_schedule`로 동일 job의 미종결 실행이 있으면 tick을 합친다.
외부 provider는 실측 13시간 sweep을 보호하는 16시간 상한을 유지하고, KMA·AirKorea·보존
정리는 2시간, regional은 6시간으로 회수 상한을 구분한다. 이 값은 처리량 SLO가 아니며
메모리 개선 후 운영 실측으로 다시 조정한다. worker/launcher 장애는 멱등 job에 한해 1회
재시도한다. provider 오류는 해당 라이브러리의 기존 제한된 재시도와 다음 정상 tick이 처리한다.

`0018_sync_run_owner`는 내부 수집 행에 Dagster run ID를 연결한다. recovery sensor는 60초
간격으로 terminal worker의 `running` 행을 CAS로 회수한다. 재시도 시작 전에도 확인한다.
`started_at/run_id` keyset으로 bounded page를 순회하여 생존 첫 페이지 뒤의 행도 확인한다.
살아 있는 worker와 조회 장애 때는 회수하지 않는다. 없는 ID는 heartbeat도 3시간 만료된
경우에만 회수하며 UPDATE 시 heartbeat를 다시 확인한다. 소유권 없는 기존 행은 3시간 lease로
정리한다. 늦게 돌아온 worker는 heartbeat·publish CAS에서 실패하고 회수 상태를 덮지 못한다.
수집 시작/reconcile의 DB lock·statement·connection 대기에도 상한을 둔다.

## 메모리와 부분 게시

KMA 격자·중기·특보는 약 5,000 normalized fact가 모이면 location별 짧은 transaction으로 게시하고 버퍼를
비운다. peak는 batch + 한 location의 response이며, 한 응답의 크기는 기존 provider 예산이
제한한다. 전체 run의 normalized fact 예산은 버퍼를 비워도 감소하지 않는다.
raw-only 응답도 KMA/외부 provider 모두 25개 source에서 비운다. 외부 provider는 raw payload
누적 10MB에서도 게시하며 한 응답 크기는 기존 예산으로 제한한다.
외부 provider는 이전 50,000개 버퍼를 5,000개로 줄였다. chunk 목록도 전체를 만들지 않고
하나씩 만든다. job의 multiprocess step 동시성은 1이다.

수집 중간에 실패하면 이미 commit한 fact·raw payload·lineage·누적 count를 보존한다.
재수집은 unique key/upsert로 같은 fact를 중복 저장하지 않는다. 전체 실행을 한 transaction으로
묶는 all-or-nothing 계약은 사용하지 않는다. ingest batch의 상한은 전체 RSS 보증이 아니므로
worker·provider response·SQL serialization을 포함한 운영 peak RSS를 후속 실측한다.
특보의 skip 재시도도 각 batch 안에서 수행한다. 특정 notice가 빠진 location은 이후 notice의
게시가 성공해도 incomplete로 기록하며, 전체 skip/starved 판정에는 location ID만 유지한다.

동일 192,000 fact를 생성하는 합성 KMA sweep의 `tracemalloc` peak는 전체 누적
446,740,271 bytes, 5,000개 batch 12,766,381 bytes였다(약 97% 감소).
DB 저장소는 count만 보존하는 시험 대역이며 전체 process RSS/운영 실측은 아니다.

## 배포와 중단 시 복구 순서

1. common 후보 SHA를 포함한 weather lock으로 image를 빌드하고 migration을 먼저 적용한다.
2. 실제 daemon의 instance 설정에 `run_monitoring`·`run_retries`·취소 상한을 반영한다.
   standalone의 정본은 `deploy/dagster.yaml`이다. shared plane에서는 그 파일이 사용되지
   않으므로 [common instance 계약](https://github.com/digitie/kor-travel-common/tree/codex/dagster-recovery/packages/py/kor-travel-common#instance-설정)을 관리자 배포 설정에 병합한다.
3. `kortravelcommon/project=weather` 동시 6개, `kortravelcommon/job` 값별 1개,
   기존 `kortravelweather/run_group=external_weather` 3개 제한을 함께 유지한다.
   pools가 있는 instance는 `concurrency.runs`에 넣고 coordinator 설정과 혼용하지 않는다.
4. recovery sensor가 RUNNING인지 확인한다. 신규 metadata DB에도 기본 RUNNING이다.
5. `/admin/dagster`의 공용 운영 화면에서 실패 원인·job별 상한 초과·스케줄을 확인한다.
   수동 재실행/취소는 해당 run의 Dagster 링크에서 인증된 운영자가 수행한다.
6. 반복 장애는 자동 재시도 1회에서 끝낸다. 원인을 수정하면 다음 정상 schedule 또는 수동
   재실행으로 회복한다. 수동 취소는 자동으로 되살리지 않는다. 오래된 QUEUED backlog는
   운영자가 의도를 확인하고 취소하며 이번 코드가 임의로 삭제하지 않는다.

daemon/code server가 내려가면 schedule·monitoring·sensor 모두 실행되지 않으므로 서비스
관리자의 restart/healthcheck가 선행이다. metadata 장애 때 run을 임의로 실패 처리하지 않는다.
run monitoring의 강제 실패 후 OS process가 남는 경우 launcher/container의 실제 종료를 확인한다.
이번 작업은 운영 서비스 교체·장애 주입을 수행하지 않았다.

## 공용 UI

로그인·메뉴·Dagster 표시 UI는 `@kor-travel/ui`를 사용한다. 인증·GraphQL scope·URL과
job label은 weather에 남긴다. tarball은 frontend `vendor`에 고정하고 Docker dependency
stage에도 넣는다. 공용 UI에서 HTTP 호출이나 타 앱의 run을 조회하지 않는다.
실패 로그는 Dagster 서버의 1,000개 page 상한에 맞춰 cursor로 조회한다(최대 20 page,
조회 시작 후 20초 안에 다음 page를 시작하며 요청마다 10초 상한). 원인 조회 실패는
FAILURE 목록을 유지하고 원인 미확인으로 표시한다.
