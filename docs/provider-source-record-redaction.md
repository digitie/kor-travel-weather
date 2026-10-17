# Provider source record 중복 순회 축소

## 범위와 설계

기준 main은 dde76a2e0356cfb69f23c95a2aff7c78c5ca738d(PR107 병합)이다.
providers/base.py와 해당 회귀·동일 작업량 벤치만 소유한다.
공개 source_record_key는 raw payload의 redaction을 계속 수행한다.
make_source_record 내부에서는 이미 정제된 payload/endpoint/request_params를
다시 재귀 복사하지 않고 동일 canonical JSON/SHA 경로로 전달할 수 있는지 측정한다.
내부 helper는 공개 unsafe 플래그나 캐시를 추가하지 않는다.
공개 typed status_code 이외의 기존 허용 입력도 identity가 바뀌지 않도록 음성 회귀를 둔다.

## 구현 전 수용

- synthetic OpenMeteo hourly336 응답과 raw6000행에 동일 입력·반복수로 CPU/tracemalloc peak를 비교한다.
- canonical key/전체 반환값/fetched_at/Unicode·URL·시간·Decimal 및 원본 불변을 이전 함수와 대조한다.
- 중첩 key·URL query secret은 동일하게 가리고 공개 raw source_record_key 계약을 유지한다.
- 실제 provider 호출·데이터량·API DTO/DB·격자 구현·Common 변경은 하지 않는다.
- 개선이 확인되지 않으면 제품 패치를 넣지 않는다.

현재는 설계 단계다. 아직 성능 개선·회귀 PASS·운영 효과를 주장하지 않는다.
로컬 core 검증만 수행하고 full PostgreSQL gate는 root가 별도로 실행한다.
