# W4 수집 상태 통합

## 범위와 현재 단계

상태표 기반 PR #149 뒤에서 native 수집 실행의 terminal CAS, 실제 응답 시각,
중단 복구, 상태 REST를 연결한다. W5 PR #148의 0029 뒤에 nullable 실행 scope
열만 추가하는 0030을 둔다. 기존 이력·수집 결과와 기존 API를 바꾸지 않는다.
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

복합 KMA 수집은 네트워크 호출 전에 실제 요청 데이터셋만 native 실행에 기록한다.
실제 terminal CAS에 성공한 실행만 상태를 투영한다. worker 중단/lease 만료의 복구도
동일한 경계를 사용한다. 이미 종료된 실행의 늦은 finish는 상태를 다시 쓰지 않는다.
상태표는 최신 시작 시각·run_id fence로 이전 실행의 결과가 새 결과를 덮지 못하게 한다.

작은 파생 쓰기는 savepoint, statement 최대 5초·lock 최대 1초를 사용한다. 더 엄격한
기존 상한은 유지한다. 파생 쓰기 실패는 경고로 기록하고 기존 수집 transaction을
계속 사용할 수 있게 한다. 이 경로는 마지막으로 **기록된** terminal을 제공하며,
상태 저장 실패 뒤의 재투영/운영 경고 수용은 rollout 검토에서 별도로 확인한다.

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
gzip을 지원한다. DB 연결은 JSON 직렬화·압축 전에 반환한다.

## 남은 수용 조건

- 실제 스케줄 평가와 next_scheduled_at 연결 및 stopped/unknown 만료 검증
- 정상·실패·별칭 재수집·중단 복구·동시 실행·상태 잠금 실패의 PostgreSQL 회귀
- 실제 HTTP/기존 UI, OpenAPI/TypeScript, 전체 CI, 독립 적대 리뷰 2명
- Map 담당에게 최종 PR·응답 예시 전달 후 consumer 계약 수용
- 운영 배포 전 기존 장기 수집 drain 및 rollout 비용·복구 검증
