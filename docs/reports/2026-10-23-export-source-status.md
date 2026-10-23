# W4 수집 상태 projection

## 범위

`weather_export_source_status`는 provider/dataset/forecast_style별 작은 상태표다.
fact 이력·raw payload·Dagster workspace를 요청마다 조회하지 않고 상태를 읽기 위한
기반이며, 기존 수집/조회 동작이나 raw 보존 정책을 바꾸지 않는다.

이 PR의 모듈만으로 `/v1/service/exports/status`가 활성화되지는 않는다.
collector terminal/schedule writer와 REST/ETag/gzip/고정 pagination 연결은 API 소유자가
통합한다. API가 계속 503인 상태를 W4 완료나 운영 배포로 보고하지 않는다.

## writer 계약

- `SourceKey`는 provider, dataset_key, forecast_style을 명시한다. 같은 provider/style의
  서로 다른 dataset을 한 행으로 덮지 않는다. source 선택/normalizer/cron 계산은
  해당 소유자의 기존 public 경로를 사용한다.
- `record_terminal`은 native run의 terminal CAS가 성공한 경우에만 호출한다.
  caller transaction 안의 savepoint를 사용하고 상태 projection 오류를 격리한다.
  이 파생 표의 쓰기 실패가 원본 수집 성공과 terminal commit을 되돌리면 안 된다.
- `(started_at, run_id)`가 저장된 값보다 새로울 때만 조건부 upsert한다.
  옛 worker의 지연 실패/성공과 같은 terminal의 재호출은 projection을 덮지 않는다.
  옛 실행의 사실은 기존 native run에 그대로 남는다.
- 성공 시 실제 `source_collected_at`을 전달해야 하며, `finished_at`은 원천 수집 시각을
  대신하지 않는다. 원본 재사용으로 수집 시각이 run 시작보다 이전인 경우도 보존한다.
  실패/partial은 마지막으로 승인된 성공 시각과 수집 시각을 유지한다.
- native 수집 성공 상태와 export generation 공개 성공은 서로 다른 단계다.
  이 표의 성공만으로 Map 데이터 endpoint가 준비됐다고 판단하지 않는다.
- `record_schedule`은 소유자가 알고 있는 다음 예정 시각을 별도 평가 시각 fence로
  저장한다. 없거나 중지된 일정은 명시적으로 null을 쓴다. API에서 cron을 해석하거나
  매번 Dagster에 접속하지 않는다. 지난 예정 시각은 조회 시 null로 보인다.
- 원문 오류·비밀·raw payload는 이 표에 저장하지 않는다.

## reader와 통합 조건

`read`는 중복 없는 명시 scope 1~128개를 PK 조건의 SELECT 한 번으로 읽으며,
없는 scope도 상태가 없는 항목으로 돌려준다. 작은 성공 부분집합을 전체로 오인해
빠진 provider/style을 숨기지 않는다. 읽기 transaction/statement timeout은 caller가
기존 export와 같이 설정한다. 모듈은 내부 commit을 하지 않는다.

API 소유자는 required dataset들을 provider/style로 묶을 때 missing/partial/failed를
숨기지 않고 공통 `source_status`를 판정해야 한다. 마지막 성공이 없는 scope는
준비되지 않은 상태다. 성공과 실패 시각이 함께 있는 경우 `last_attempt_failed`는
최신 native terminal을 기준으로 한다. 고정 pagination/304는 이 상태와 누락 여부도
ETag에 포함해야 한다. 다음 예정 시각이 null인 경우 추정값을 만들어내지 않는다.

## migration과 검증

0028은 0027 예보 index 다음에 빈 파생 상태표만 만든다. 이력을 스캔하는 backfill이
없다. downgrade는 해당 상태표만 제거한다. 0029 일간 요약은 별도 작업이다.

검증은 PostgreSQL에서 replay/지연 worker fence/실패 시 이전 성공 보존/rollback/
dataset 격리/KST 입력/다음 일정 중지·만료/PK 단일 SELECT 경계를 확인한다.
두 적대 리뷰·CI·REST 통합·실제 API/UI·운영 비용 검증 전에는 완료로 간주하지 않는다.
