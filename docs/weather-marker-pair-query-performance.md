# Marker fact 복합 키 재조회

기준: Weather98 fd737d56a043dead878efc535a77a140cd4fd412. 소스 조사 단계이며 운영 개선은 아직 검증하지 않았다.

## 확인한 구조

지도는 위치60개씩 최대3요청을 병행하고 API는 최대500개의 명시 location_id만 받는다. 빈 ID는 fact 조회를 수행하지 않는다. 기존 observed/fallback은 metric별 최신 후보를 LATERAL로 제한한 후 value_id만으로 fact를 다시 읽고, alert window의 최종 fact JOIN도 value_id만 사용한다. 그러나 fact의 실제 primary/partition key는 (value_id, known_at)이다.

## 최소 변경

후보 관계와 alert 제한 관계에 known_at을 보존하여 fact를 두 키로 JOIN한다. 기존 metric allowlist·observed 우선/fallback·known/source/value tie-break·alert station/sequence grouping·명시 valid_until 및 max-age·위치별 한도·DTO와 순서를 유지한다. raw/source/upsert/수집 로직, API timeout/UI 병렬도/retry/schema/index는 바꾸지 않는다.

원격 계획과 실행시간은 root가 bounded read-only로 따로 측정한다. known_at 동등 JOIN이 있어도 planner가 항상 모든 partition을 제거한다고 미리 주장하지 않는다. 같은 UUID의 서로 다른 known_at은 DB primary key가 허용하므로 선택한 pair의 정확성을 회귀로 검증한다.

## 검증 상태

추가 회귀·전체 required checks·독립2리뷰·운영 계획/시간·live UI는 진행 전이다. 이전 R19 marker504는 이 소스가 해결한 결과로 계산하지 않는다.
