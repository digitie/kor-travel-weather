# 성공 export publisher 자동화와 terminal 복구

## 구현 범위와 수명

기준 제품은 `72970ccf5e86665569ddd9456b8075d4b2bc93aa`다. 원본 working tree를
수정하지 않고 별도 `codex/export-publisher-recovery`에서 작업한다.

확정된 lifecycle `REJECTED`가 head 승격 단계에서 `NOT_READY`로 바뀌던 경계를
보존한다. 후속 publisher는 native 실패/partial 또는 정확한 Dagster
`FAILURE`/`CANCELED` 증거만 확정 실패로 처리한다. 원천 부분 저장과 이전 성공
head를 보존하고, 불확실한 orchestrator/metadata 유실은 대기 상태로 남긴다.

자동화는 별도 worker/job를 실행하지 않는 `weather_export_publisher_sensor`다.
기본 STOPPED와 기존 `service_exports_enabled=false`를 유지하며 artifact 경로가
명시된 환경에서만 평가한다. 최소 180초 간격, 2개씩 microbatch, UUID keyset 재개
cursor를 사용한다. 한 sweep을 마치면 cursor를 비워 이전에 대기하던 후보와 새로
생긴 후보를 다시 확인한다. tick당 최대 256개/128회와 12초 협력 예산을 함께 적용하며
후보 조회 시간도 예산에 포함한다. 총량 상한은 운영 처리율 약속이 아니다.
한 프로세스의 nonblocking slot과 DB별 고정 advisory
lease를 함께 사용한다. lease용 connection 1개는 유지하지만 원본/승격 transaction은
Dagster 상태 조회와 파일 I/O 전에 반환한다. 매 tick 소유한 engine은 dispose한다.
sensor는 Definitions에 등록한다. 운영에서 명시 start하는 단계는 별도 배포 gate이며
이 PR은 운영을 활성화하지 않는다.

확정 native 실패·partial·cancelled·abandoned는 attempt를 failed로 바꿔 반복 선택하지
않는다. Dagster `FAILURE`/`CANCELED`로 죽은 worker를 확인하면 native **running**만
같은 잠금 transaction에서 failed로 바꾸며 저장된 fact 개수는 보존한다. W4 원장은
`export_status_pending`을 통해 기존 repair 경로로 다시 투영한다. 이미 terminal인
native success/partial은 다시 쓰지 않는다. 따라서 native 성공 뒤 Dagster 후속 실패는
attempt 실패와 native 성공을 별도로 보존한다. W4 status는 native 수집 상태이며
export 승인 상태가 아니다. current/forecast/daily reader는 같은 scope의 최신 attempt
실패도 검사하므로 이전 승인 자료를 보존하되 stale로 응답한다. 이전 healthy ETag로
304를 내지 않으며 이미 발급한 cursor에서도 stale 상태를 유지한다.
파일 누락/손상은 확정 실행 실패로
취급하지 않으며 원래 파일을 복원한 뒤 다음 sweep에서 승격할 수 있다.

`NOT_READY`가 된 unknown/STARTED/metadata 조회 실패는 시간 경과만으로 reject하지
않는다. Dagster SUCCESS가 먼저 보이고 native가 running인 구간도 대기한다. metadata
조회는 슬롯 1개와 2초 deadline을 사용하며 늦은 조회는 승격/실패를 기록하지 않는다.
SQL은 기존 head transaction의 statement 5초와 ingest 잠금 상한을 유지한다. lease
획득/해제 문장은 2초 상한이며 해제에 실패한 connection은 invalidate해 pool에
session lock을 남기지 않는다. 오류 로그에는 exception 원문/DSN/provider payload를 쓰지 않는다.

## 검증과 남은 gate

별도 소유 PostgreSQL DB에서 초기 terminal 14건이 15.53초, 실제 public Dagster
`evaluate_tick`을 포함한 초기 24건이 27.73초에 통과했다. 초기 집중 회귀는 32건이
39.23초에 통과했고, 앞선 publisher/heads/daily 통합 회귀는 72건이 81.39초에
통과했다. 두 실행은 중복 시험을 포함하므로 합산해 서로 다른 104건으로 세지 않는다.
Ruff 전체와 `git diff --check`도 통과했다. 실제 Dagster instance의
SUCCESS/FAILURE/CANCELED/STARTED/없는 run과 native/attempt 결과를 대조했고
두 번 평가해도 새 run/worker가 생기지 않는 것을 확인했다. advisory lease 충돌,
비활성 설정의 DB 생성 0회, 오류 시 cursor 보존/engine 반환도 포함한다.
원본 파일 복원 후 승격, 새 epoch 재시도, 실제 callback 2초 timeout의 미확정 보존,
native running 실패의 W4 pending/기존 repair 재투영과 마지막 성공 시각 보존도 검사했다.
API 추가 회귀 2건(초기 32건에 포함)은 current/forecast/daily 3종의 old healthy ETag
무효화·자료 보존·기존 cursor stale를 실제 앱/TestClient로 검증했다.
이는 로컬 격리 검증이며 운영 활성화·공개 live UI·CPU/RAM 개선 실측을 뜻하지 않는다.
두 독립 적대 리뷰, 최종 CI와 운영 start gate는 아직 미완이다. 이 PR에는 프런트엔드
수정이 없으며 새 UI 동작을 검증했다고 주장하지 않는다.
파일 검증·마지막 SQL 대기와 연결 획득까지 포함한 전체 wall-clock12초를
보장한다는 뜻은 아니며 예산·문장/잠금 상한의 실제 경계를 최종 보고에 남긴다.

운영 start gate는 API/CS 설정과 artifact 경로·읽기 권한의 일치, 정의 등록 및 migration
상태, exact SUCCESS 조회 권한, 두 리뷰와 CI, 제한된 실제 수집/승격/실패 후 복구 검증이다.
이 PR의 sensor 기본 상태는 STOPPED이며 daemon의 다른 schedule/sensor 상태를 바꾸지 않는다.

## plan 선언 전 crash 복구

초기 리뷰에서 begin/claim 뒤 plan INSERT 전 crash가 후보 INNER JOIN에서 제외되는
경계를 실제 sensor/PG 시험으로 재현했다(기대 1개, 실제 0개). 후보를 LEFT JOIN으로
찾아 plan 없는 실행도 확정 terminal은 복구하며, plan 없는 성공/미확정은 승격하지 않는다.
begin 직후와 claim 직후 각각 FAILURE/CANCELED/SUCCESS/STARTED/없는 run을 검증한
중간 집중 실행은 42 PASS(50.51초)다. 이후 용량/소유권 보강과 최종 검증은 별도다.

## 용량과 성공 이력의 반복 조회 방지

고정 2개/tick은 KMA 회당 300격자를 따라가지 못하므로 작은 microbatch를 시간/총량
상한 안에서 이어 처리한다. 일별 파생 집계는 batch마다 반복하지 않고 sweep 마지막에
최대 한 번 시도한다. 실제 owner 표의 모든 scope가 더 큰 epoch와 다른 active attempt로
바뀐 경우만 이전 후보를 SUPERSEDED로 퇴역한다. native 원장/fact/새 owner/head는
바꾸지 않는다. 일부 scope만 이동했거나 owner가 누락/동일 epoch면 추측하지 않는다.

`0032_export_publication_pending`은 nullable `published_at`과 pending partial index를
추가한다. generation/head 승격과 같은 transaction에서 표식을 기록하며 실패하면 함께
rollback한다. 기존 성공 이력은 실제 generation.created_at으로 한 번만 backfill하고
매 tick은 `published_at IS NULL`인 preparing/sealed만 순서대로 찾는다. anti-generation
조건도 방어적으로 유지한다. 배포는 기존 instigator drain·NVMe 절차 후 수행하며 migration
statement는 최대 60초/잠금은 최대 5초(더 짧은 caller 상한 보존)다. timeout이면 전체
migration transaction을 rollback하고 drain/데이터 크기를 확인한 뒤 재시도한다.

중간 집중 50 PASS(69.99초)는 300개 합성 PG 후보를 두 tick의 256/44개로 재개하는
선택/상한 검증을 포함한다. 이 시험은 clock을 고정했으므로 처리율/CPU/RAM 실측이 아니다.
추가 queue/schema 4 PASS(9.57초)에서 실제 5,000 published+1 pending의 원래 조회 SQL을
EXPLAIN ANALYZE하고 pending index 사용/실제 결과 1개를 확인했다. 승격 표식 오류 시
generation/head rollback, 0031 downgrade→0032 upgrade와 정확한 publication 시각 backfill,
신규 DB 전체 migration/열·인덱스 계약도 검사했다. 전체 standalone `alembic check`는
기존 일별 partition/FK를 제거 대상으로 표시하므로 그 실행을 PASS로 세지 않는다.
이후 최종 통합 회귀·최종 두 리뷰·CI는 별도 정산한다. 운영 지속 처리량과 ARM 비용은
기본 STOPPED를 해제하기 전 반드시 실측해야 한다.
