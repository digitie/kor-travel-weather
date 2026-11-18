# 예보 페이지의 누적 artifact 입력 예산 — 2026-11-19

일반 grouped 예보(`short`·`ultra_short`·`external`)는 DB 창마다8MiB를 지켰지만 한 페이지에서 창을 여러 번 채웠다. 이제 페이지 전체의 실제 artifact 입력에8MiB 예산을 적용한다. batch의 선읽기 전체를 검산된 `byte_count`로 세고, 이미 읽은 창의 소비는 다시 세지 않는다. 단일 읽기도 같은 예산을 쓴다. 다음 ref를 가져오기 전에 멈추므로 `has_more=true`와 아직 읽지 않은 위치의 cursor로 이어진다.

요청 `limit`은 계속 출력 상한이다. byte 상한 때문에 더 짧은 페이지가 생기고 전체 페이지 수가 늘 수 있다. 과거 target만 담은 chunk를 소진하면 빈 페이지도 cursor를 앞으로 진행시킨다. 첫 단일 artifact가 page 예산보다 크면 진행을 허용하지만 기존 artifact hard bound·SHA·bytes·point count 검산은 그대로다. 기본 page 예산은 기존 artifact/window 상한과 같은8MiB다. paired 중기예보 reader, current/daily, publication·GC·snapshot/cursor 형식·신선도·ETag 판정은 변경하지 않았다.

## 검증

새 시험4건이 변경 전7e 코드에서 실제 읽기 초과로 실패했다: 누적10KiB 예산에25KiB, 기본8MiB에10MiB, 단일 oversized artifact 뒤의 추가 읽기2건. DB에서 받은 모든 payload의 실제 길이를 기록하므로 소비된 항목만 세어 선읽기를 숨기지 않는다.

예산 변경의 새16건을 포함한 관련 export/API 시험173건과 저장소 전체 Ruff가 통과했다. 누적 창 refill, batch/단일 읽기, 첫 oversized 진행, limit1/200/2000 전체 walk의 순서·정밀도·누락/중복 없음, 빈 페이지 진행, since 제외, hard bound 유지, warm decoded cache 뒤 SHA/bytes/points/삭제 변조 실패를 확인했다. 기존 cursor limit binding·만료·시간 경계·projection·metadata 시험도 포함한다.

## 요청별 검산 입력 증거

기존 worker `ReadTrace`의 ContextVar에 정수 `forecast_input_bytes`·`forecast_input_refs`만 추가했다. DB reader가 **실제 받은 payload 전체**의 SHA·byte 길이·point 수를 검산한 직후 `len(payload)`와1 ref를 기록한다. batch의 미소비 선읽기도 모두 포함하며 projection/cache hit도 DB 검산을 생략하지 않는다. 전역 counter delta나 출력 항목 수를 요청 입력으로 대신하지 않는다.

DB grouped forecasts의 첫/cursor/304 응답에 다음 header를 추가한다. JSON·ETag·신선도 계약은 바꾸지 않는다.

| Header | 의미 |
| --- | --- |
| `x-export-forecast-input-trace: db-verified-v1` | 이 worker 요청에 귀속된 DB 검산 입력 |
| `x-export-forecast-input-bytes` | 실제 검산한 payload byte 합계 |
| `x-export-forecast-input-refs` | 실제 검산한 ref 횟수(고유 ref 수/모델 수 아님) |
| `x-export-forecast-input-budget-bytes` | 이 grouped page의 입력 예산8MiB |

재사용304는 bytes/refs를 명시적0으로 낸다. 첫 단일 oversized 예외는 refs=1과 bytes>budget으로 식별할 수 있고 기존 hard artifact 상한은 유지한다. 기본8MiB는 기존 단일 artifact hard 상한과 같으며 시험에서 더 작은 page 예산으로 진행 예외를 검증했다. paired 중기와 파일 backend에는 이 DB 예산 proof header를 붙이지 않는다. 실패한 검산은 성공한 입력으로 세지 않는다.

추가3건이 hook/header 없는 코드에서 RED였다. 변경 후 관련 DB/trace/API 시험100건, 기본 정상8MiB first/304 header2건이 통과했다. 실제 병렬 worker8KiB/8refs와2KiB/2refs의 분리,1개만 소비한 window의8개 선읽기 전부 귀속, single 읽기·잘못된 receipt 거부, 실제 API의 oversized→cursor 전체 walk→304/0을 확인했다. 이 증거는 단위/fixture 증거이며 배포 후 header 수치는 아직 관측하지 않았다.

## 작은 실제 PG fixture 비교

정확7e의 이전 reader/window와 신규 reader를1MiB짜리10 artifact fixture에서3회 교대로 실행했다. 매회 process-local decoded cache만 비웠고 각 첫 페이지에 실제 요청 ReadTrace를 적용했다. DB/storage cold 상태나 HTTP·freeze·메타데이터 지연을 재현한 시험이 아니다.

| 첫 페이지 | 변경 전 | 변경 후 |
| --- | ---: | ---: |
| 실제 payload 입력 | 10MiB /10 chunks | 8MiB /8 chunks |
| 출력 위치 | 10 | 8, 다음 페이지2 |
| wall 중앙값 | 220.4ms | 184.9ms |
| CPU 중앙값 | 96.0ms | 74.8ms |

6개 첫 페이지 표본 모두 request-local 검산 bytes/refs가 별도로 관측한 실제 DB payload 길이/행 수와 일치했다. 전체 walk의10개 항목 JSON SHA256은 둘 다 `4479aca66512fcfc6a3ef37fb0c1fd3e9504e0cc1c120a5961cdb31ecdea7753`였다. 전체 walk의 총 입력10MiB를 없애는 변경이 아니라 한 요청의 입력을 제한하는 변경이다.

저장된7e 관측의 약125MB/128 chunks는 global counter delta여서 동시 reader 기여를 포함할 수 있다. 이번 국소 fixture 결과를 production 개선율로 환산하지 않는다. freeze·metadata 비용은 남으며 production 첫 페이지1초 달성은 미입증이다. 운영 HTTP·build·deploy·migration은 실행하지 않았다.

실행한 관련 회귀 시험:

```text
pytest -q tests/test_export_forecast_input_budget.py \
  tests/test_export_database_read_window.py tests/test_export_read_window_refill.py \
  tests/test_export_forecast_projection.py tests/test_export_reader_lifetime.py \
  tests/test_export_read_cost.py tests/test_export_inventory_forecasts.py \
  tests/test_export_forecast_metadata.py \
  packages/kor-travel-weather-api/tests/test_frozen_service_exports.py \
  packages/kor-travel-weather-api/tests/test_service_exports.py
ruff check .
```
