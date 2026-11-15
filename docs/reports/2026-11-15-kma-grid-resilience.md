# KMA 격자 수집의 부분 장애 격리

## 발견한 중단 경로

`fetch_group`에서 예외가 발생하면 순서대로 결과를 소비하던 `_prefetch_grids`가
중단되고 이미 시작한 뒤 격자까지 취소했다. SDK의 유한 재시도가 끝난 한 격자의
일시 통신 오류가 정상 격자의 미수집으로 확대되는 구조였다. 정상 빈 응답도 앞선
격자 검증에서 일반 예외로 처리되어 같은 중단 경로를 탔다.

또한 예보의 한 격자는 여러 위치 chunk로 저장된다. 앞 chunk가 이미 commit된 뒤
다음 chunk가 잠금 오류를 내거나 반환 응답이 유실되면, 호출자가 가진 반환값의 합은
실제 저장 건수보다 작다. 이 오류를 단순히 무시하고 기존 합으로 종료하면 저장된
자료는 남아도 실행 이력의 건수가 틀리므로, 잠금 격리 전에 계수 경계를 보강했다.

## 반영한 회복 경계

- 공개 SDK의 `network`, `server`, `parse` 오류는 SDK 재시도 소진 후 해당 격자만
  유예하고 뒤 정상 격자를 계속 처리한다. 빈 정상 응답은 `PROVIDER_NO_DATA`로
  구분한다. 실패 프레임에는 고정 코드와 격자 식별자만 담고 원문·예외 traceback은
  유지하지 않는다. 선행 요청 네 개와 응답 크기·행·fact 예산은 그대로다.
- 인증, 일일 할당량, 초당 제한, 잘못된 요청, 알려지지 않은 예외와 HTTP
  401/403/429는 공통 원인으로 중단한다. 앱에서 SDK 재시도를 중첩하거나 제공기관
  오류를 문자열로 재해석하지 않는다. 실제 초당 제한 분류와 rate 설정은 별도
  SDK·운영 변경의 책임이다.
- export가 격자별로 직접 게시하는 경로에서만 SQLSTATE `55P03`, `40P01`,
  `40001`의 기존 DB 재시도 소진을 격리한다. 정상 후속 격자는 계속 저장한다.
  여러 격자가 섞인 legacy staging batch, DB 연결·디스크·권한 장애, 소유권 상실은
  격자별로 안전하게 분리할 수 없으므로 실제 실패를 유지한다.
- 부분 게시된 격자는 전체 예상 fact 수를 예산에서 차감한다. commit된 부분만
  추측하여 예산을 재사용하지 않는다. 준비된 export attempt는 게시 성공 후에만
  목록에서 제거하므로 실패 격자도 종료 정리에서 거절된다.
- `publish_and_finish(values_loaded_offset=None)`은 각 ingest transaction이 run 행에
  누적한 DB 계수를 조건부 종료 UPDATE의 행 잠금 안에서 유지한다. 반환 건수도 이
  값을 사용한다. 기존 정수 offset과 기본값 0의 의미는 바꾸지 않았다. 새 표·설정·
  전역 잠금·재시도 루프를 추가하지 않았다.

## 상태와 신선도

정상 전체 처리는 `success`, 정상 격자를 저장했지만 일부 격자를 유예한 실행은
`partial`이다. 모든 격자가 조회·게시되지 못하면 `KMA_ALL_GRIDS_FAILED`의 실제
`failed`로 남는다. 모든 값이 결측인 경우의 기존 `KMA_ALL_VALUES_SKIPPED`도
실패로 유지한다. 취소와 소유권 상실은 부분 성공으로 바꾸지 않는다.

`planned_grids`, `completed_grids`, `failed_grids`, `deferred_grids` 숫자와
`grid_failure_counts`, `deferred_grid_ids`, `previous_data_preserved`를 결과에 남긴다.
유예는 별도 DB 실행 상태가 아니라 이번 실행의 처리 결과다. 다음 수집은 기존 정기
일정·대상 회전·예산을 사용하며 새 큐나 즉시 전체 재수집을 만들지 않는다.

정상 위치의 raw·fact는 저장해도 불완전 cohort를 전체 최신 export로 승인하지 않는다.
실패 격자에 0개 관측을 만들거나 이전 head의 기준 시각을 갱신하지 않는다. 기존 정상
head는 보존한다. 따라서 Dagster 실행 자체가 종료되어도 관리 화면에서는 `partial`을
정상 전체 성공과 구분해야 한다.

## 검증과 한계

초기 WSL 집중 검증은 100개 통과·빈 응답 2개 실패였다. 실제 실패를 보존하고 앞선
빈 응답 검증도 같은 명시적 결측 예외로 연결한 뒤 WSL·Docker에서 각각 102개 통과했다.
이후 DB 잠금 격리와 authoritative 계수 보강을 포함한 최종 집중 109개가 WSL·Docker에서
각각 통과했다(각 기존 경고 54개, 45.92초·47.06초). Ruff 전체 선택 파일과
`git diff --check`도 통과했다. 시험 중 소유 Python 코드의 SHA가 같고, 전용 PG는
정확한 전체 CID·소유 label 확인 뒤 종료·제거했으며 QA container 잔여는 0개다.
최종 원장은 Git 밖 `weather-qa/grid-resilience-final-receipt.json`이다. 겹치는 시험
수는 합산하지 않는다.

회귀에는 앞·중간 격자 실패 후 나머지 처리, 전체 제공기관 실패, 인증·제한의 전체
중단, 상위 취소 뒤 선행 task 종료, 이전 export head 보존, chunk 중간 잠금과
COMMIT 반환 유실 뒤 실제 DB 계수 보존을 포함한다. 운영 API 호출·수집·배포나 속도
보장 실측은 수행하지 않았다. `20rps`와 선행 격자 네 개도 이 변경에서 바꾸지 않았다.

## 일일 할당량과 초당 제한의 진단 구분

기존 공개 오류 코드 `PROVIDER_RATE_LIMIT` / `ALERT_PROVIDER_RATE_LIMIT`는 유지하고,
SDK의 구조화 분류에 따라 관리자 metadata만 구분한다. `quota`는
`rate_limit_scope=daily`, `retryable=false`, `recovery_hint=WAIT_QUOTA_RESET`이다.
`rate_limit` 또는 HTTP 429는 `rate_limit_scope=per_second`, `retryable=true`,
`recovery_hint=RETRY_AFTER_BACKOFF`이다. 일일 할당량 분류가 있으면 HTTP 429가
같이 있어도 일일 할당량을 우선한다. 인증·일반 오류·전체 결측 분류는 그대로다.

특보 오류의 허용 목록에는 이 두 scope·두 복구 지침과 정확한 boolean만 추가했다.
제공기관의 오류 메시지·URL·`resultCode`를 앱에서 파싱하거나 앱 재시도를 늘리지
않는다. SDK의 실제 `resultCode=23` 정규화·유한 재시도 변경과 exact pin 소비는
별도 SDK 작업이며 이 앱 진단 검증과 구분한다.

추가 집중 검증은 공개 SDK 예외를 통한 일반 수집·특보의 인증/할당량/초당 제한,
비밀 문자열과 예외 cause/context 차단, metadata 허용 목록과 Dagster 전달을 대상으로
WSL·Docker 각각 27개 통과했다(각 기존 경고 38개, 5.34초·6.37초). Ruff·diff 검사와
시험 중 소유 runtime SHA 동일성을 확인했고, 별도 소유 PG도 전체 CID·label 검산 뒤
종료·제거하여 잔여가 없다. 기존 109개 검증 원장은 바꾸지 않았다. 추가 원장은
Git 밖 `weather-qa/limit-diagnostics-final-receipt.json`에 기록했다.

## 정상 종료와 경고의 목록 관측

KMA 격자 asset와 지역 특보 asset는 원래 상태를 바꾸지 않고 같은
`collection_outcome`을 출력 metadata에 남긴다. 자기 Dagster 실행에는 `partial`과
`deferred` 경고만 `kortravel/collection-outcome` 태그로 기록한다. `success`는 태그를
읽거나 쓰지 않으므로 동시·후속 정상 op가 앞선 경고를 지울 수 없다. 이미 관측한
경고보다 낮거나 같은 경고도 다시 쓰지 않는다. 태그 읽기·쓰기가 실패하면 고정
`COLLECTION_OUTCOME_TAG_DEFERRED`만 기록하고 정상 수집 결과는 유지한다.

공개 태그 API에는 CAS가 없어 동시 `partial`·`deferred` 사이의 순서는 보장하지 않는다.
두 값 모두 경고이며 정확한 격자·원천별 상태는 각 op의 metadata가 정본이다. 경고 태그가
없다는 사실을 전체 데이터의 최신화 보장으로 해석하지 않는다. 새 전역 잠금이나 스키마는
추가하지 않았다.

이 helper와 두 asset 연결만 대상으로 WSL·Docker에서 각각 16개 통과했다(각 기존 경고
27개, 4.68초·5.62초). 선택 파일 Ruff 통과, 시험 중 runtime SHA 동일·전용 PG 종료/제거·
잔여 0을 확인했다. 앞선 109개·27개 시험은 다시 실행하지 않았으며,
추가 원장은 `weather-qa/outcome-tags-final-receipt.json`이다.
