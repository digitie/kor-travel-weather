# 외부 weather provider migration

기존 KMA fact와 source record는 변경하지 않는다. 새 provider는 다음 순서로
활성화한다.

1. 환경별 secret manager에 provider key를 등록한다. `.env`와 fixture에 실제 key를
   커밋하지 않는다.
2. `KOR_TRAVEL_WEATHER_ENABLED_PROVIDERS`에 provider key를 추가한다. `open_meteo`와
   `wttr_in`은 key 없이도 사용할 수 있다.
3. admin의 provider/dataset catalog와 Dagster `external_weather_job`에서 응답을
   확인한다. `hourly_airkorea_weather`가 측정소 catalog/관측을 갱신하고,
   `hourly_external_weather`가 마지막으로 성공한 AirKorea anchor를 사용해
   활성 provider를 매시간 실행한다. AirKorea 일시 장애가 기존 외부 수집을
   막지 않도록 두 schedule은 독립적으로 실행된다.
4. 성공한 응답만 source record와 normalized facts로 atomic publish한다. 실패한
   실행은 failed sync run만 남기며 기존 immutable fact를 변경하지 않는다.

외부 provider는 AirKorea 측정소를 공통 anchor로 사용한다. `KOR_TRAVEL_WEATHER_ENABLED_PROVIDERS`
에 넣은 provider만 `hourly_external_weather`에서 실행되며, quota가 있는 provider는
admin provider credential을 먼저 설정한다. 필요하면 `external_weather_job`을
수동 launch해도 같은 budget/lineage 경계를 사용한다.

기존 `weather_values`/`weather_source_records` schema migration은 필요 없다. 새
dataset은 기존 source lineage의 `provider`, `dataset_key`, `source_record_key` 축을
사용한다. `WeatherValue.identity_key()`는 source response key를 revision으로
포함하므로 같은 fixture를 replay해도 추가 fact가 생기지 않는다.
