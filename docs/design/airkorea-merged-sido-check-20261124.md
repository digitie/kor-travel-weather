# AirKorea 통합 주소 측정소의 원천 시도 대조 (#282 후속)

## 배경

#282는 측정소 목록 주소 첫 토큰이 `전남광주통합특별시`인 측정소를 `광주`·`전남` 두 시도별
bulk 묶음(`getCtprvnRltmMesureDnsty`)에 모두 넣고 **이름만으로** 맞췄다
(`src/kortravelweather/providers/airkorea.py`의 `measurement_sido_names`,
`_MERGED_SIDO_PREFIXES`). 광주 도심 13곳 누락은 고쳤지만 이름만 보는 매칭에는 두 구멍이
남았다.

1. **교차 연결.** 광주 anchor `X`와 같은 이름의 전남 측정소가 있는데 그 전남 anchor가
   활성 목록에 없으면(측정소 상한 밖이거나 운영자가 비활성화) 전남 묶음에서 `X` 후보가
   광주 anchor 하나뿐이다. 이번 시각 광주 묶음에 `X`가 없거나 광주 묶음 요청이 실패하면
   전남 `X`의 관측이 광주 anchor에 그대로 붙는다. 모호성 검사(`len(candidates) != 1`)로도
   걸리지 않는다.
2. **불필요한 격리.** 반대로 두 anchor가 모두 활성이면 두 묶음 모두에서 후보가 둘이라
   두 측정소가 매 run `ambiguous_name`으로 빠진다.

## 규칙

원천 측정 행의 시도 필드를 anchor의 시도와 대조한다.

- **원천 행의 시도**: AirKorea 행의 `sidoName`. `python-airkorea-api`의
  `parse_measurement`가 `AirQualityMeasurement.sido_name`으로 옮긴다. 모델 속성이 없으면
  `raw["sidoName"]`을 본다(`measurement_sido_name`). 행에 시도가 없으면 요청한 묶음의
  시도로 본다(시도별 bulk 응답 계약). `광주광역시` 같은 긴 이름은 짧은 이름으로
  정규화하지만, 통합 명칭(`전남광주…`)은 어느 한 시도로 줄이지 않아 대조 실패로 드러난다.
- **anchor의 시도**: `anchor_sido_names`. 통합 주소가 아니면 지금까지의 시도
  (`measurement_sido_names`)와 같다. 통합 주소는 주소 두 번째 토큰으로 통합 전 시도
  하나를 가린다(`_MERGED_DISTRICT_SIDOS`).

  | 두 번째 토큰 | 시도 |
  | --- | --- |
  | 동구·서구·남구·북구·광산구 | 광주 |
  | 목포시·여수시·순천시·나주시·광양시, 전남 17개 군 | 전남 |
  | 그 밖(표에 없음, 토큰 없음) | 광주·전남 둘 다(가리지 못함) |

  저장된 `measurement_point.sido_name`은 통합 주소에서 첫 토큰 접두어로 정해진 `전남`이라
  쓰지 않는다. anchor metadata는 그대로 둔다(insert-only).
- **묻는 묶음은 그대로다.** 통합 주소 anchor는 계속 두 묶음에 들어간다. 어느 묶음이 돌려준
  행이든 그 행의 `sidoName`이 anchor 시도와 같아야 붙인다. AirKorea가 묶음 배치를 바꿔도
  행의 시도 필드로 판단한다.
- **두 단계로 붙인다.** 모든 묶음 응답을 받은 뒤 anchor별로 붙인다
  (`packages/kor-travel-weather-dagster/src/kortravelweather_dagster/airkorea_weather.py`).
  - 같은 이름 후보 가운데 행 시도를 받아들이는 anchor가 하나면 그 anchor에 묶는다. 같은
    시도의 같은 행이 두 묶음에서 오면 한 번만 쓴다.
  - 받아들이는 anchor가 없으면 이름이 같았던 anchor에 `sido_mismatch`를 남긴다.
  - 둘 이상이면 기존처럼 `ambiguous_name`으로 격리한다.
  - 시도를 가리지 못한 anchor가 서로 다른 시도 행 둘을 받으면 `ambiguous_name`이다.
  - 시도를 가리지 못한 anchor는 묻는 묶음 하나라도 요청이 실패했으면 붙이지 않는다.
    못 받은 묶음에 같은 이름이 있었는지 알 수 없어서다. 사유는 그 묶음의
    `request_error`로 남는다.

## 실패 분류

`sido_mismatch`는 결정적 실패다(`TRANSIENT_REASONS`에 넣지 않음). #282의 분류를 그대로
따른다.

- 결과 metadata `station_failure_reasons`, `stations_failed_deterministic`, 표본.
- sync run 오류 문자열(`N개 측정소 수집 실패 (sido_mismatch=…)`).
- Dagster 결과 `status`/`collection_outcome` = `partial`. native `weather_sync_runs`는
  success로 둬 받은 측정소의 승격은 그대로다.
- export 수집기가 있으면 거절 attempt의 `error_code`도 실제 사유를 남긴다
  (`SOURCE_COVERAGE_UNCONFIRMED:sido_mismatch`). 전에는 사유와 무관하게 `not_in_response`였다.
- bulk가 없는 client의 측정소별 경로(`fetch_station_measurement`)도 시도 대조 실패를
  `StationSidoMismatch`(`ValueError` 하위)로 올려 `request_error`(일시) 대신
  `sido_mismatch`(결정적)로 센다.

한 anchor에 여러 사유가 생기면 처음 사유가 남는다(#282와 같음). 다른 묶음에서 제 관측을
받으면 앞선 사유는 지운다.

## 남는 위험

- 두 번째 토큰 표는 행정구역 표다. 통합특별시가 하위 구역 이름을 바꾸면 그 anchor는
  "가리지 못함"으로 떨어진다. 이 경우에도 다른 시도 행 둘이 오면 격리되고, 요청 실패 시에는
  붙이지 않는다. 남는 경우는 제 묶음에서 이번 시각 빠졌는데 다른 시도에만 같은 이름이 있는
  때뿐이다. `_SIDO_ALIASES`처럼 개편 때 사람이 갱신한다(`docs/integration-map.md`).
- AirKorea가 통합 뒤 행의 `sidoName`을 새 명칭으로 바꾸면 통합 주소 측정소 전부가
  `sido_mismatch`로 빠진다. 조용히 잘못 붙는 대신 partial로 드러나며, 그때 이 표와 대조
  규칙을 다시 본다.
- 측정소별 경로의 기대 시도는 지금처럼 주소 첫 토큰 그대로다. 이 경로는 bulk가 없는
  client와 시도를 알 수 없는 anchor만 탄다. 운영 client는 bulk를 쓰므로 바꾸지 않았다.

## 배포 뒤 확인

첫 `airkorea_weather_job` run의 `station_failure_reasons`에 `sido_mismatch`가 있는지 본다.
있다면 표본 `location_id`의 측정 원천(`payload.measurement.sidoName`)과 anchor 주소 두 번째
토큰을 대조한다. #282로 드러난 광주 13곳이 계속 수집되는지도 같이 본다.
