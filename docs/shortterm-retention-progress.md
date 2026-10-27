# 단기예보 정리의 무진행 연기와 실패 관측

> 2026-10-27부터 Failure는 **진척 0인 실행**과 **남은 backlog의 지연 상한이 24시간을 넘는 실행**에만 남긴다.
> 지운 행이 있고 지연이 24시간 안이면 backlog가 남아도 SUCCESS이며, 다음 실행은 검사한 materialization
> metadata 위치에서 이어 정리한다. SKIP LOCKED는 lock_timeout 대기로 바뀌었다.
> 현재 정본은 [보존정책의 2026-10-27 절](shortterm-retention.md#2026-10-27-상시-실패와-이어-읽기)이며 아래는 당시 기록이다.
> BATCH_COMMITTED 관측 이벤트의 8개 필드와 기록 시점은 그대로다.

구현 범위는 두 가지다. SKIP LOCKED로 선택한 행이 없지만 만료 행이 남으면
기존 deferred 상태로 즉시 반환하여100회 무진행 조회를 피한다. 완료로 표시하지 않는다.

Dagster 작업은 배치별 확정 커밋 숫자를 표준 AssetObservation 이벤트에 남기고, 명시적 미완료 Failure에
안전한 결과 metadata를 포함한다. SQL 예외는 그대로 실패하며 이미 커밋한 배치를
되돌렸다고 표시하지 않는다.

과거 valid_at 6시간 경계와 미래 무제한 보존, 정확 복합키 삭제 순서,
advisory/row lock, 부분 index, SQL/lock/작업 예산과10분 주기는 변경하지 않는다.
초기 구현의 base는 main dde76a2이며 PR108의 provider source-record 변경과 파일이 겹치지 않는다.

## 결과와 실패 경계

runner는 matched_in_batch=0/remaining=true이면 status=deferred로 멈춘다.
빈 전체 선택(remaining=false)은 complete_snapshot, dry-run은 dry_run으로 유지한다.
잠긴 행이 없어졌거나 처리되었다고 추측하지 않으며 다음 정기 작업이 남은 행을 다시 고른다.

Dagster는 BATCH_COMMITTED 뒤 해당 asset의 표준 AssetObservation 이벤트를 즉시 기록한다.
metadata는 phase·operation_id·batches·matched_in_batches·facts_deleted·pointers_deleted·
remaining·elapsed_seconds의8개 필드로 한정한다. 기존 INFO 로그도 유지하지만 진행 기록의
근거는 Python 로그 수준과 무관한 관측 이벤트다. ERROR/WARN에서 INFO가 저장되지 않아도
후속 SQL 예외 전에 기록된 이벤트를 같은 persistent InstanceRef로 다시 읽을 수 있다.
아직 commit되지 않은 intent나 SQL 예외 메시지·DSN·원자료는 이 metadata에 넣지 않는다.
이전 이벤트는 앞 batch가 commit을 반환했다는 근거이며 실패한 현재 batch의 commit 여부나
관측 이벤트 저장소 자체의 장애까지 대신 증명하지 않는다. 도메인 DB commit과 Dagster
이벤트 저장은 하나의 원자적 transaction이 아니므로 그 사이 실패를 성공으로 추측하지 않는다.

complete_snapshot 이외의 명시적 Failure에는 result metadata를 포함한다.
따라서 add_output_metadata 뒤 STEP_OUTPUT이 나오기 전에 실패해도 Failure 이벤트에서
status와 누적 수치를 확인할 수 있다. SQL 예외는 그대로 전파하고 실패를 성공이나
예산 소진으로 바꾸지 않는다. 부분 commit을 전체 rollback으로 표현하지 않는다.

## 초기 runner/Failure 검증

초기 구현 때 DB fixture를 제외한 별도 임시 디렉터리에서 기존 순수 회귀와 새 실제 asset 함수/
Dagster Failure 대역 시험을 실행했다. 구현 전6FAIL/53PASS가 요구한 경계에서
실패하는 것을 보존했고 구현 후60PASS/25warnings(1.04초), 전체 Ruff 통과를 확인했다.
경고는 설치된 Dagster/Pydantic 호환 계층의 deprecated 필드 사용이다.
실제 SQL·이벤트 저장소·운영 성능을 시험한 결과가 아니며 PostgreSQL 전체 시험과
필수 frontend 확인은 root의 별도 통합 검증 대상이다. 본 작업은 DB/SDK 운영 호출이나
배포를 수행하지 않았다.


## INFO 차단 환경에서의 진행 보존 보완

root의 실제109 수용 실행은 complete_snapshot/SUCCESS였지만 공용 Python 로그 수준 WARN에서
배치 INFO가 저장되지 않았다. 운영 설정을 바꾸지 않고 BATCH_COMMITTED에 명시적 관측 이벤트를
추가한다. 기존 구현에 대한 자기 임시 SQLite/실제 Dagster1.13.24 재현에서도 첫 배치의
확정 반환 후 두 번째 OperationalError가 발생하면 persisted commit INFO와 관측 이벤트가
모두0이었다. 이 원래 실패 증거를 보존한다.

추가 회귀는 도메인 DB 대신 fake repository를 사용하고 자기 임시 SQLite DagsterInstance만
연다. ERROR와 WARN 각각에서 첫 배치 뒤 SQL 예외/run FAILURE를 유지하면서, instance dispose와
from_ref 재개 후 앞 배치의 관측 metadata와 STEP_FAILURE 이전 순서를 확인한다. SQL 예외를
Failure 결과나 성공으로 변환하지 않으며 기존 미완료 Failure metadata 정책도 유지한다.
운영 PostgreSQL·SDK worker·설정·배포를 실행한 결과가 아니다.


추가 검증은 sterile 환경에서 기존 runner/asset 회귀와 새 저장소 회귀를 합쳐63PASS/
25기존warnings(2.06초), 전체 Ruff PASS다. 새 회귀 첫 실행의2FAIL은 관측 이벤트의 SDK
접근자를 함수와 혼동한 테스트 오류이며 원로그를 보존하고 접근자만 정정했다. 제품의
원 INFO 차단 재현은 별도 보존한다. 변경은 definitions의 이벤트 기록·집중 테스트·이 문서
세 파일이며 retention runner/repository/공용 YAML/DTO는 그대로다.
