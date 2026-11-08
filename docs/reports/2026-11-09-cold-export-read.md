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

회귀와 성능 경로의 WSL PG18 127개/동일 Docker PG18 127개는 통과했다(겹침 합산 없음). 새 원자적 pin 경계의 후속 WSL은 1FAIL/56PASS였으며, SQL INSERT hook 안에서 동기 writer를 실행하던 시험이 공유 pin guard와 자기 경합했다. 이 원문을 보존하고 실제 별도 writer와 GC의 장벽으로 보정한 뒤 검증한다. 최초 테스트 DB를 명시하지 않은 보조 실행은 localhost15432 준비 오류3개로 종료했으며, 정식 소유 PG를 지정한 위 결과와 구분한다. 기존 Ruff의 관련 없는 407행 E501도 새 진단으로 고치지 않았다.

## 실제 배포

새 API·CS 이미지는 아직 빌드/배포 전이다. PR209 writer 변경은 수집기도 소비하므로 같은 검증 소스를 최소 API/CS 이미지에 반영한다. 웹·의존성·DB0039는 그대로 유지하고 migration을 다시 하지 않는다. 수집기 교체는 원 Weather27 일정을 보존·일시중지하고 기존 작업이 자연 종료한 뒤 수행한다. Transport20·MCP 외부 HTTPS·공용 daemon·다른 서비스·키/환경은 보존한다. 백업·복원은 사용자 지시로 생략한다.

정본: Git 밖 `weather-cold-read-20261109/plan.json`, 원 프로파일·후보 원문·실제 최초 Invocation 종료·소유 자식 정산을 연결한다. 배포 뒤 실제 CID/image/source·Manager·일정 복원·첫 부모 종료·HTTP 결과를 추가한다.
