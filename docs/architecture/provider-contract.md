# Provider contract

provider adapter는 외부 client의 응답 shape만 정규화하며, 공통 transport 경계가
인증·재시도·HTTP 오류를 분류한다. adapter는 live credential을 source payload에
기록하지 않고, `ProviderResponse(source_record, values)`를 반환한다. KMA의 기존
`python-kma-api` 경로와 `WeatherValue`/source lineage 저장 계약은 그대로 유지한다.

| dataset | client call | normalized styles | 주요 metric |
| --- | --- | --- | --- |
| `kma_ultra_short_nowcast` | `KmaClient.now` | nowcast | T1H, REH, WSD, VEC, RN1, PTY, SKY |
| `kma_ultra_short_forecast` | `KmaClient.forecast.short` | ultra_short | TMP, POP, PTY, SKY, WSD… |
| `kma_short_forecast` | `KmaClient.forecast.vilage` | short | TMP, TMN, TMX, POP… |
| `kma_mid_forecast` | `DataGoKrClient.mid_*` | mid | SKY, POP, TMN, TMX |

외부 provider catalog는 다음 stable key를 사용한다.

| provider | dataset key | 인증 | 단위/시간대 |
| --- | --- | --- | --- |
| `weatherapi` | `weatherapi_current`, `weatherapi_forecast` | `WEATHERAPI_API_KEY` | metric, provider timezone → UTC |
| `openweathermap` | `openweathermap_current`, `openweathermap_forecast` | `OPENWEATHERMAP_API_KEY` | metric, epoch UTC |
| `open_meteo` | `open_meteo_current`, `open_meteo_forecast` | 없음 | deg C/mm/%/m/s, UTC 요청 |
| `visual_crossing` | `visual_crossing_timeline` | `VISUAL_CROSSING_API_KEY` | metric, timezone → UTC |
| `tomorrow_io` | `tomorrow_io_realtime`, `tomorrow_io_forecast` | `TOMORROW_IO_API_KEY` | SI, ISO-8601 → UTC |
| `weatherbit` | `weatherbit_current`, `weatherbit_forecast` | `WEATHERBIT_API_KEY` | metric, timestamp → UTC |
| `weatherstack` | `weatherstack_current` | `WEATHERSTACK_API_KEY` | metric, timezone → UTC |
| `accuweather` | `accuweather_current`, `accuweather_forecast` | `ACCUWEATHER_API_KEY` | metric, ISO/epoch → UTC |
| `wttr_in` | `wttr_in_current`, `wttr_in_forecast` | 없음 | metric, location timezone → UTC |

모든 provider의 canonical metric은 `TEMP`, `FEELS_LIKE`, `HUMIDITY`, `PRESSURE`,
`WIND_SPEED`, `WIND_DIRECTION`, `PRECIP`, `PRECIP_PROB`, `CLOUD_COVER`,
`VISIBILITY`, `UV_INDEX`, `WEATHER_CODE`이며 속도는 `m/s`, 기온은 `deg_c`,
강수량은 `mm`, 습도·확률은 `%`, 풍향은 `deg`로 저장한다.

camelCase와 snake_case fixture를 모두 허용하되 필수 필드 누락은 명시적으로 실패한다.
지원하지 않는 category는 quarantine/실패하고, `"1mm 미만"`은 숫자 0과 원문 qualifier를
함께 보존한다. 중기 육상(`wf3Am`, `rnSt3Am`)과 기온(`taMin3`, `taMax3`)은 day/period
window로 fan-out한다.
