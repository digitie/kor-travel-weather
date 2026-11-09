# 2026-11-09 최초 export 구성·리베이스 후속

## 변경과 검증 범위

최신 main `f337b6f`(PR209)의 파일 단위 공유 잠금과 writer 재시도를 통합했다. 기존 UI·의존성·Alembic 이력을 보존하며 API의 짧은 원자적 파일 검산·snapshot pin commit도 유지한다. 원격 feature branch의 이전 정본은 `e430b1f`, 리베이스 직후는 `e406709`다. 백업 ref와 미커밋 변경 stash는 Git 밖 정산 원장에 보존한다. main에는 쓰지 않는다.

실제 CI의 27FAIL/1795PASS와 리베이스 뒤 17FAIL/75PASS를 보존했다. `Session`을 받는 snapshot 참조 저장의 dialect 조회를 고쳤고, wire 시각을 넣기 전에 DTO 검증을 수행해 잘못된 입력의 ValidationError 계약을 복원했다. 60초 허용 구간·정확한 만료 뒤 실패/회복·입력 meta 불변을 가상 시계로 검산했다. 실패 검사를 지우거나 cache.clear로 결과를 만들지 않았다. 이 회귀 보정은 `554dafc`다.

최신 cohort run을 원천별 top-1 scalar subquery 한 문장으로 조회한다. 시작 시각·run ID 정렬, 실패·진행 run, cohort 필수 여부와 같은 RR을 유지한다. 일별 첫 페이지는 roster·coverage·요약 값을 같은 RR에서 읽고, 그 요청과 connection 안에서만 coverage 검산을 한 번 수행한다. 다른 요청/cursor로 memo를 넘기지 않는다. catalog key는 값·형식·부호 구분을 유지하면서 같은 필드를 두 번 꺼내 문자열화하던 비용을 줄였다. API의 새 snapshot은 전체 파일 존재·크기 검사와 ref commit을 GC guard 안에서 이미 끝낸 사실만 transient 표식으로 전달하여 중복 stat을 피한다. 재사용·fallback·SDK의 파일 검사와 재시도는 유지한다.

## 실제 관측

동일 운영 DB·artifact를 사용하는 별도 ASGI 프로세스의 관측이며 서비스 교체나 외부 재수집은 하지 않았다. 프로세스의 첫 요청이지 OS cold·네트워크 HTTP·p95 보장은 아니다.

| 관측 | 현재100개 | 일별10개 |
| --- | ---: | ---: |
| 원 운영 소스, cProfile 포함 | 49.404초200 | 24.106초200 |
| 리베이스 후보, cProfile 포함 | 97.897초503 | 15.025초200 |
| 후속 후보, cProfile 없이 첫 요청 | 29.262초200 | 6.222초200 |
| 같은 프로세스의 조건부 재사용 | 45.50ms304 | 62.77ms304 |

프로파일의 누적 시간은 겹치므로 더하지 않는다. 프로파일 후보 current의 `ExportGuardTimeout`과 최대60초 상태 지연 거부를 성공으로 바꾸지 않았다. 계측·부하·기존 snapshot 재사용 상태가 다르므로 위 숫자를 코드 단독 개선율로 환산하지 않는다. 현재100개 item SHA `3085291e5fe2b8a6258d7ea6c11785e0979e26f153c8d7e50d6cc4350324724f`와 원천 기준 시각 `2026-11-08T09:03:26.448664Z`·stale는 같았다. 최초1초 목표는 미달이다.

회귀와 성능 경로의 WSL PG18 127개/동일 Docker PG18 127개는 통과했다(겹침 합산 없음). 새 원자적 pin 경계의 후속 WSL은 1FAIL/56PASS였으며, SQL INSERT hook 안에서 동기 writer를 실행하던 시험이 공유 pin guard와 자기 경합했다. 이 원문을 보존했다. 실제 별도 writer의 flock 경합→commit된 ref 가시성→실제 GC의 orphan 삭제와 pin 보존, SDK의 commit 전 실제 삭제→재발급/일시 거부를 검산하여 WSL58개(70.51초)·Docker58개(72.79초)가 통과했다. 최초 테스트 DB를 명시하지 않은 보조 실행은 localhost15432 준비 오류3개로 종료했으며, 정식 소유 PG를 지정한 위 결과와 구분한다. 전체 CI의 Lint 단계까지 확인하기 위해 기존 branch의 줄 길이·import 간격 진단13개만 보정했다. 전후 AST가 같고 전체 Ruff는 통과했다. 제품 이미지8723afc와 동작이 같으므로 이 서식 보정으로 이미지를 다시 만들지 않는다.

## 실제 배포

제품 `8723afcf54997d3c449edc7d0558ab36f092cb54`의 API `f3f7a4a8`·CS `68c093c9` 최소 이미지를 빌드했다. 최초 Invocation `502b9889b5a747238ff629e782e13faf`의 실제 done·Main0·cgroup부재·소유 자식 종료와 이미지 내부 source SHA/설정/0039 head 검증을 완료했다. PR208은 명시 lease로 feature branch를 갱신하고 base를 main으로 옮겼으며 Draft를 유지한다.

새 API는 CID `810b488581aae44d123013198b851b30c9531c4c2e402673c61c3dc808f403ec`/실제12201로 교체되어 healthy·실행 소스 SHA·Settings5초·키일치·DB0039를 확인했다. 실제 serving API HTTP는 health/version200·미인증401, current 최초52.435초200/조건부172.79ms304, daily13.114초200/조건부99.70ms304, 60초 상태 만료 뒤 current23.840초200이었다. current100개 item SHA·원천 시각·stale는 같다. 최초1초·전체 속도 목표를 달성한 것으로 쓰지 않으며 별도 ASGI 프로세스의29.262/6.222초와 구분한다. HTTP 보조의 실제 자식0·종료도 확인했다.

자연 대기 배포 Root49e389는 사용자의 강제 중지 지시에 따라 정산했다. 첫 Invocation b3db의 실제1/Cancelled·원27복원·Main0/cgroup부재를 보존한다. 첫 강제 후속 ca2a77/첫Invf61e도 STARTING 작업의 SAFE_TERMINATE 거부로 실제1이며, 원27복원·Main0/cgroup부재를 확인했다.

최종 단일 Root `weather-forced-r2-deploy-20261109-c42411c886`은 정상 G→R/FD9에서 공식 `MARK_AS_CANCELED_IMMEDIATELY` 정책을 사용했다. Weather4건의 요청은 모두 TerminateRunSuccess였고 실제 최종 상태는3CANCELED/1FAILURE다. native terminal과 실제 old worker0을 확인한 뒤 수집기만 교체했다. app 실행 이력은 이미failed/499,968건이었고 canonical 회수는0으로 그 값을 유지했다. 다른 실행을 임의로 실패 처리하거나 원자료를 삭제하지 않았다.

최종 API f3f7a4a8/CID810b4885·포트12201과 수집기68c093c9/CIDb1626cea·포트12206은 제품8723afc/healthy다. 실제 source SHA·실효 키/Settings5초·DB0039·원27복원·Manager healthy를 확인했다. 첫 Invocation `8a75bb1f2b2949898fde4003ecc087a5`의 실제0/done·Main0·cgroup부재·모든 소유 자식 종료를 검산했다. 반영 뒤 health/version200과 `python -I -m kortravelcommon.grpc_health 12206 --check live` 실제0도 확인했다. Healthcheck 설정 자체는 이 작업에서 바꾸지 않았다. API·웹·Transport·외부MCP·공용daemon·다른 서비스·환경/키는 배포 경계에서 보존했다.

정본: Git 밖 `weather-cold-read-20261109/plan.json`, 원 프로파일·후보 원문·실제 최초 Invocation 종료·소유 자식 정산을 연결한다. 최종 정본 `weather-cold-read-20261109/final-acceptance.json`의 SHA는 `5596c9bc70fa11f02977c32e663224c2df7371baea07a1c00ce075c5c84afefe`다. 사용자 강제 지시·공식 취소 결과·각 실패/성공 최초 Invocation을 별도로 결박했다.


## 오래된 PR 정산

#193·#197·#199는 PR208과 운영 API 제품의 원본 조상이고, #204의 리베이스 통합은 원본과 최신main의 자동3-way merge와 모든 실행 경로가 같다. 보고서·journal/resume/tasks-done·0037도 보존돼 있다. 각 PR에 근거를 남겨 PR208로 대체하여 닫았으며 원본4 branch/ref·커밋은 유지했다. 별도 main 머지는 하지 않았다.
