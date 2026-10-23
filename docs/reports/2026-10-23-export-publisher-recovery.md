# 성공 export publisher 자동화와 terminal 복구

## 초안 범위

기준 제품은 `72970ccf5e86665569ddd9456b8075d4b2bc93aa`다. 원본 working tree를
수정하지 않고 별도 `codex/export-publisher-recovery`에서 작업한다.

확정된 lifecycle `REJECTED`가 head 승격 단계에서 `NOT_READY`로 바뀌던 경계를
보존한다. 후속 publisher는 native 실패/partial 또는 정확한 Dagster
`FAILURE`/`CANCELED` 증거만 확정 실패로 처리한다. 원천 부분 저장과 이전 성공
head를 보존하고, 불확실한 orchestrator/metadata 유실은 대기 상태로 남긴다.

자동화는 별도 worker/job를 실행하지 않는 작은 Dagster sensor로 구현한다.
기본 STOPPED와 기존 `service_exports_enabled=false`를 유지하며 artifact 경로가
명시된 환경에서만 평가한다. 최소180초 간격, 작은 batch/재개 cursor, 단일 lease,
SQL/잠금 상한과 repository 수명 회수를 검증한다. sensor를 Definitions에 등록하고
운영에서 명시 start하는 단계는 root 소유이며 이 PR은 운영을 활성화하지 않는다.

## 미완 검증

초안 단계다. 실제 PostgreSQL terminal/새 epoch 복구·부분 보존·동시 tick·sensor
평가 회귀, Ruff, 두 독립 적대 리뷰 및 전체 CI를 완료한 것으로 표시하지 않는다.
파일 검증·마지막 SQL 대기와 연결 획득까지 포함한 전체 wall-clock12초를
보장한다는 뜻은 아니며 예산·문장/잠금 상한의 실제 경계를 최종 보고에 남긴다.
