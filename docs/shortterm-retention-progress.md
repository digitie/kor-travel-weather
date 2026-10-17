# 단기예보 정리의 무진행 연기와 실패 관측

구현 범위는 두 가지다. SKIP LOCKED로 선택한 행이 없지만 만료 행이 남으면
기존 deferred 상태로 즉시 반환하여100회 무진행 조회를 피한다. 완료로 표시하지 않는다.

Dagster 작업은 배치별 확정 커밋 숫자를 로그에 남기고, 명시적 미완료 Failure에
안전한 결과 metadata를 포함한다. SQL 예외는 그대로 실패하며 이미 커밋한 배치를
되돌렸다고 표시하지 않는다.

과거 valid_at 6시간 경계와 미래 무제한 보존, 정확 복합키 삭제 순서,
advisory/row lock, 부분 index, SQL/lock/작업 예산과10분 주기는 변경하지 않는다.
최신 main dde76a2 기준이며 PR108의 provider source-record 변경과 파일이 겹치지 않는다.

## 결과와 실패 경계

runner는 matched_in_batch=0/remaining=true이면 status=deferred로 멈춘다.
빈 전체 선택(remaining=false)은 complete_snapshot, dry-run은 dry_run으로 유지한다.
잠긴 행이 없어졌거나 처리되었다고 추측하지 않으며 다음 정기 작업이 남은 행을 다시 고른다.

Dagster는 BATCH_COMMITTED 뒤 operation_id·batch수·누적 fact/projection 삭제수·남은 행 존재·
elapsed 숫자만 로그에 남긴다. 아직 commit되지 않은 intent나 SQL 예외 메시지·DSN·원자료는
이 로그에 포함하지 않는다. 마지막 batch 실패 전의 로그는 앞 commit의 근거이며
실패한 현재 batch의 commit 여부까지 대신 증명하지 않는다.

complete_snapshot 이외의 명시적 Failure에는 result metadata를 포함한다.
따라서 add_output_metadata 뒤 STEP_OUTPUT이 나오기 전에 실패해도 Failure 이벤트에서
status와 누적 수치를 확인할 수 있다. SQL 예외는 그대로 전파하고 실패를 성공이나
예산 소진으로 바꾸지 않는다. 부분 commit을 전체 rollback으로 표현하지 않는다.

## 검증

DB fixture를 제외한 별도 임시 디렉터리에서 기존 순수 회귀와 새 실제 asset 함수/
Dagster Failure 대역 시험을 실행했다. 구현 전6FAIL/53PASS가 요구한 경계에서
실패하는 것을 보존했고 구현 후60PASS/25warnings(1.04초), 전체 Ruff 통과를 확인했다.
경고는 설치된 Dagster/Pydantic 호환 계층의 deprecated 필드 사용이다.
실제 SQL·이벤트 저장소·운영 성능을 시험한 결과가 아니며 PostgreSQL 전체 시험과
필수 frontend 확인은 root의 별도 통합 검증 대상이다. 본 작업은 DB/SDK 운영 호출이나
배포를 수행하지 않았다.
