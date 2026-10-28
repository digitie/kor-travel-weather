# W4 수집 상태 통합

## 범위와 현재 단계

상태표 기반 PR #149 뒤에서 native 수집 실행의 terminal CAS, 실제 응답 시각,
중단 복구, 상태 REST를 연결한다. W5 PR #148의 0029 뒤에 nullable 실행 scope
열과 재투영 대기 flag·부분 인덱스를 추가하는 0030을 둔다. 기존 이력·수집 결과와
기존 API를 바꾸지 않는다.
현재 구현·검증 중이며 운영 활성화 또는 전체 Map API 완료를 뜻하지 않는다.

`service_exports_enabled` 기본값은 false다. 활성화한 repository만 상태를 투영한다.
모든 provider의 정적 catalog 중 현재·예보 데이터셋을 사용하며 측정소 목록과 특보는
제외한다. KMA 실황은 nowcast, 외부 예보는 external로 표시한다. 데이터셋별 실패가
같은 provider의 다른 데이터셋을 성공으로 덮지 않도록 응답에 dataset_key를 포함한다.

## 원천 시각과 소유권

원본 payload 재사용으로 source_record_key가 기존 행의 별칭으로 바뀌기 **전** 실제
이번 `fetched_at`을 실행·provider·데이터셋별 최소/최대 시각으로 기록한다. fact마다
상태를 쓰지 않고 ingest batch마다 그룹당 한 번 쓴다. 실제 시각이 없으면 null이며
native 종료 시각을 수집 시각으로 대체하지 않는다.

복합 KMA 수집은 네트워크 호출 전에 실제 요청 데이터셋을 native 실행의 같은 INSERT에
기록한다. opt-in 복합 수집에서 명시 계획이 없거나 지원하지 않는 데이터셋이면 실행을
시작하지 않는다. 별도 best-effort 계획 setter를 사용하지 않는다.
실제 terminal CAS에 성공한 실행만 상태를 투영한다. worker 중단/lease 만료의 복구도
동일한 경계를 사용한다. 이미 종료된 실행의 늦은 finish는 상태를 다시 쓰지 않는다.
상태표는 최신 시작 시각·run_id fence로 이전 실행의 결과가 새 결과를 덮지 못하게 한다.

작은 파생 쓰기는 savepoint, statement 최대 5초·lock 최대 1초를 사용한다. 더 엄격한
기존 상한은 유지한다. 파생 쓰기 실패는 경고로 기록하고 기존 수집 transaction을
계속 사용할 수 있게 한다. native terminal CAS의 **같은 UPDATE**에 실행별 재투영
대기 flag를 남기고 투영 성공 때 지운다. 별도 대기표 INSERT가 실패해 복구 의도를
잃는 경로를 제거했다. 상태 행 경합 때문에 투영을 못 해도 대기 flag는 남는다.
상태 조회는 pending=true 부분 인덱스에서 대기 실행 한 건만 복구를 시도하며, 해당
provider에 미처리 실행이 있으면 옛 성공에 304를 주지 않고 503을 반환한다. 다음
조회에서 잠금이 풀렸으면 복구한다. 사실 이력 전체 스캔과 수집 재실행은 하지 않는다.
flag는 재투영이 성공할 때만 지워진다. native 실행 정리가 flag를 따로 지우는 경로는 없다
(2026-10-27 정정). dataset 개명·제거나 SourceKey 모양 변경으로 저장 scope가 현재
catalog와 맞지 않으면 재시도해도 같으므로, 아직 유효한 scope만 기록하고 flag를 지우며
`weather_export_status_scope_repaired`를 남긴다. 상태 행 잠금·기록 실패는 flag를 남겨
다음 조회가 다시 시도한다. 전체 실행 이력은 스캔하지 않는다.
부분 인덱스와 조회 조건을 모두 `export_status_pending = true`로 맞춘다. 완료 실행
5,000건과 대기 한 건을 둔 실제 PostgreSQL EXPLAIN에서 이 부분 인덱스 사용을 확인했다.
알려진 cancelled/abandoned는 그대로 실패 상태로 표시하고, 미등록 native terminal도
파생 상태에서는 failed로 보수적으로 표시한다. native 원래 상태는 바꾸지 않는다.

해양·산악·청정넷·휴게소 opt-in 수집은 provider fetch 전에 native 실행을 만든다.
fetch timeout/권한 오류도 실패 terminal로 남긴다. 성공 시 기존 publish 경로를 같은
실행으로 이어받고 실제 대상 수를 갱신한다. 비활성 provider와 기본 OFF 경로는 기존
동작을 유지한다. provider client·원문 파싱을 재구현하지 않는다.

## REST 계약

`GET /v1/service/exports/status`는 기존 `x-admin-token` 인증을 그대로 사용한다.
최대 128개인 작은 목록 전체를 한 응답으로 반환하며 pagination은 필요하지 않다.
미구현 stub에 있던 limit/cursor를 제거했고, query를 보내면 422로 명시적으로 거절한다.
다른 큰 export의 signed cursor와 페이지네이션은 그대로 유지한다.

반환 필드는 provider, dataset_key, forecast_style, last_status, last_success_at,
last_failed_at, last_attempt_failed, next_scheduled_at이다. 아직 실행되지 않은 필수
데이터셋도 null 상태로 포함한다. terminal이 하나도 없으면 빈 성공 대신 503을 준다.
일부 데이터셋만 성공했거나 실제 수집 시각이 없으면 meta.source_status는 stale이다.
어느 데이터셋도 성공한 적이 없으면 failed이다.

상태 신선도 상한은 KMA 실황·초단기 3시간, 단기 6시간, 중기 18시간,
하루 두 번 수집하는 지역 소스 36시간, 기타 외부 소스 6시간이다. 수집 정책을 바꾸는
값이 아니라 상태 응답의 보수적 표시 기준이다. 미래 원천 시각도 stale로 처리한다.
source_collected_at은 필수 데이터셋의 실제 수집 시각이 모두 있을 때 최솟값이며,
하나라도 모르면 null이다. generated_at을 제외한 의미 기반 weak ETag로 304를 지원하고
gzip을 지원한다. 대기 flag와 상태 목록은 같은 REPEATABLE READ에서 확인한다.
DB 연결은 JSON 직렬화·압축 전에 반환한다.

실제 Dagster tick의 public evaluate_tick 결과를 위임해 기존 중복 억제와
RunRequest/config/tags를 보존한다. 다음 시각은 등록된 cron·timezone을 croniter로
계산한다. 모듈 import/definition load 때 DB를 연결하지 않는다. active 실행 때문에
새 run을 생략하는 tick도 일정은 기록한다. 시각 없는 수동 평가와 명시적 중지/불명은
null이며, 중지 후 tick이 없으면 이전 예정 시각이 지난 뒤 reader가 null로 보여 준다.
기본 default_status로 현재 실행 상태를 추측하거나 API가 workspace를 조회하지 않는다.

## 남은 수용 조건

- 수정 전 기반 회귀 83개, 원자 pending/지역 실패 보강 관련 회귀 128개 PASS.
  추가 terminal 경계 포함 상태 통합 17개와 5,000건 EXPLAIN 회귀 1개도 PASS.
  이 수는 서로 겹치므로 단순 합산하지 않는다.
- 실제 HTTP/기존 UI, OpenAPI/TypeScript, 전체 CI, 독립 적대 리뷰 2명
- Map 담당에게 최종 PR·응답 예시 전달 후 consumer 계약 수용
- 운영 배포 전 기존 장기 수집 drain 및 rollout 비용·복구 검증
