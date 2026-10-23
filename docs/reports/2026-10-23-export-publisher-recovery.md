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
명시된 환경에서만 평가한다. 최소 180초 간격, 최대 2개 후보, UUID keyset 재개
cursor를 사용한다. 한 sweep을 마치면 cursor를 비워 이전에 대기하던 후보와 새로
생긴 후보를 다시 확인한다. 한 프로세스의 nonblocking slot과 DB별 고정 advisory
lease를 함께 사용한다. lease용 connection 1개는 유지하지만 원본/승격 transaction은
Dagster 상태 조회와 파일 I/O 전에 반환한다. 매 tick 소유한 engine은 dispose한다.
sensor를 Definitions에 등록하고
운영에서 명시 start하는 단계는 root 소유이며 이 PR은 운영을 활성화하지 않는다.

확정 native 실패·partial·cancelled·abandoned는 attempt를 failed로 바꿔 반복 선택하지
않는다. Dagster `FAILURE`/`CANCELED`로 죽은 worker를 확인하면 native **running**만
같은 잠금 transaction에서 failed로 바꾸며 저장된 fact 개수는 보존한다. W4 원장은
`export_status_pending`을 통해 기존 repair 경로로 다시 투영한다. 이미 terminal인
native success/partial은 다시 쓰지 않는다. 따라서 native 성공 뒤 Dagster 후속 실패는
attempt 실패와 native 성공을 별도로 보존한다. 파일 누락/손상은 확정 실행 실패로
취급하지 않으며 원래 파일을 복원한 뒤 다음 sweep에서 승격할 수 있다.

`NOT_READY`가 된 unknown/STARTED/metadata 조회 실패는 시간 경과만으로 reject하지
않는다. Dagster SUCCESS가 먼저 보이고 native가 running인 구간도 대기한다. metadata
조회는 슬롯 1개와 2초 deadline을 사용하며 늦은 조회는 승격/실패를 기록하지 않는다.
SQL은 기존 head transaction의 statement 5초와 ingest 잠금 상한을 유지한다. lease
획득/해제 문장은 2초 상한이며 해제에 실패한 connection은 invalidate해 pool에
session lock을 남기지 않는다. 오류 로그에는 exception 원문/DSN/provider payload를 쓰지 않는다.

## 검증과 남은 gate

별도 소유 PostgreSQL DB에서 초기 terminal 14건이 15.53초, 실제 public Dagster
`evaluate_tick`을 포함한 초기 24건이 27.73초에 통과했다. 실제 Dagster instance의
SUCCESS/FAILURE/CANCELED/STARTED/없는 run과 native/attempt 결과를 대조했고
두 번 평가해도 새 run/worker가 생기지 않는 것을 확인했다. advisory lease 충돌,
비활성 설정의 DB 생성 0회, 오류 시 cursor 보존/engine 반환도 포함한다.
이는 로컬 격리 검증이며 운영 활성화·부하 개선 실측을 뜻하지 않는다.
추가 회귀와 기존 heads/daily 회귀, 두 독립 적대 리뷰, 최종 CI는 아직 gate다.
파일 검증·마지막 SQL 대기와 연결 획득까지 포함한 전체 wall-clock12초를
보장한다는 뜻은 아니며 예산·문장/잠금 상한의 실제 경계를 최종 보고에 남긴다.

운영 start gate는 API/CS 설정과 artifact 경로·읽기 권한의 일치, 정의 등록 및 migration
상태, exact SUCCESS 조회 권한, 두 리뷰와 CI, 제한된 실제 수집/승격/실패 후 복구 검증이다.
이 PR의 sensor 기본 상태는 STOPPED이며 daemon의 다른 schedule/sensor 상태를 바꾸지 않는다.
